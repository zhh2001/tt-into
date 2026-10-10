"""按 ingress、egress、配置与收报调度的固定路径 TT-INT 事件模型。"""
from dataclasses import dataclass
import heapq
import itertools

from collector.tagged_codec import HEADER_BYTES, RECORD_BYTES, STACK_SLOTS, WireRecord, decode, encode
from orchestration.event_observation import CacheState, Confirmation, CountObservation, OnlineView, ReceivedReport
from orchestration.event_schema import integer


# 同时刻先处理输入控制和读取快照，再处理业务；后续跳由前跳动态生成。
PRIORITY = {'request': 0, 'apply': 1, 'read_snapshot': 2, 'ingress': 3,
            'egress': 4, 'sink': 5, 'report': 6, 'ack': 7, 'read_available': 8}


@dataclass
class Gate:
    enabled: bool = False
    threshold_us: int = 100000
    last_local_us: int | None = None
    enabled_version: int = 0
    threshold_version: int = 0


class EventEngine:
    """预声明外部事件的执行层。在线视图不暴露这些事件或仿真真值。"""

    def __init__(self, nodes, routes, packets, writes=(), reads=(), capacity=1):
        integer(capacity, '共同 K', 1, STACK_SLOTS)
        self.capacity = capacity
        self.nodes = {n.node: n for n in nodes}
        if not self.nodes or len(self.nodes) != len(nodes):
            raise ValueError('节点为空或重复')
        self.routes = dict(routes)
        self.packets = {p.packet_id: p for p in packets}
        if len(self.packets) != len(packets):
            raise ValueError('包标识重复')
        self.writes, self.reads = tuple(writes), tuple(reads)
        self._validate()
        self.gates = {(o.node, f): Gate() for f, route in self.routes.items() for o in route}
        self.counters = dict.fromkeys(self.routes, 0)
        self.packet_state = {p.packet_id: {'records': [], 'length': p.ip_bytes, 'envelope': False,
                                           'dropped': False, 'gate_times': {}} for p in packets}
        self.queue, self.sequence = [], itertools.count()
        self.now_us = None
        self.events, self.cost_events, self.receipts, self.exports = [], [], [], []
        self.reports, self.counts, self.confirmations = [], [], []
        self.cache, self.read_values = {}, {}
        self.inband = dict.fromkeys(sorted({h.link for p in packets for h in p.hops}), 0)
        self.insertions = dict.fromkeys(sorted(self.nodes), 0)
        for p in sorted(packets, key=lambda p: (p.hops[0].ingress_us, p.packet_id)):
            self._schedule(p.hops[0].ingress_us, 'ingress', (p.packet_id, 0))
        for w in sorted(writes, key=lambda w: (w.issued_us, w.write_id)):
            self._schedule(w.issued_us, 'request', ('write', w))
            self._schedule(w.effective_us, 'apply', w)
            self._schedule(w.ack_us, 'ack', w)
        for r in sorted(reads, key=lambda r: (r.issued_us, r.read_id)):
            self._schedule(r.issued_us, 'request', ('read', r))
            self._schedule(r.snapshot_us, 'read_snapshot', r)
            self._schedule(r.available_us, 'read_available', r)

    def _validate(self):
        if not self.routes:
            raise ValueError('需要预声明管理路径')
        for flow, route in self.routes.items():
            if not route or len({o.node for o in route}) != len(route) or len(route) > 253:
                raise ValueError('管理路径为空、含环或超过 TTL')
            if any(o.node not in self.nodes for o in route):
                raise ValueError('路径节点未声明')
        for p in self.packets.values():
            if p.flow not in self.routes or tuple(h.object_id for h in p.hops) != self.routes[p.flow]:
                raise ValueError('业务包不匹配已注册固定路径')
        if len({w.write_id for w in self.writes}) != len(self.writes) or len({r.read_id for r in self.reads}) != len(self.reads):
            raise ValueError('控制操作标识重复')
        for operation in self.writes + self.reads:
            route = self.routes.get(operation.flow, ())
            if operation.node not in {o.node for o in route}:
                raise ValueError('控制对象未注册')
        for r in self.reads:
            if r.node != self.routes[r.flow][0].node:
                raise ValueError('当前计数只在源节点取得')
        for w in self.writes:
            if w.field == 'threshold_us' and w.value >= (1 << (self.nodes[w.node].clock_bits - 1)):
                raise ValueError('阈值超过局部时钟的半周期')
        for node in self.nodes.values():
            times = [t for p in self.packets.values() for h in p.hops if h.object_id.node == node.node
                     for t in (h.ingress_us, h.egress_us)]
            if times and max(times) - min(times) >= (1 << (node.clock_bits - 1)):
                raise ValueError('本轮固定时钟实例的轨迹超过半周期，生成顺序不再无歧义')

    def _schedule(self, time_us, kind, payload):
        heapq.heappush(self.queue, (time_us, PRIORITY[kind], next(self.sequence), kind, payload))

    def _log(self, kind, **fields):
        self.events.append({'time_us': self.now_us, 'kind': kind, **fields})

    def _rpc_cost(self, kind, operation, part):
        value = operation.cost.request_bytes if part == 'request' else operation.cost.reply_bytes
        self.cost_events.append({'time_us': self.now_us, 'resource': 'control', 'kind': kind,
                                 'operation_id': getattr(operation, 'write_id', getattr(operation, 'read_id', None)),
                                 'part': part, 'bytes': value, 'basis': 'serialized_rpc',
                                 'node': operation.node, 'flow': operation.flow.label,
                                 'provenance': operation.cost.provenance})

    def run_until(self, until_us):
        integer(until_us, '运行截止时间')
        if self.now_us is not None and until_us < self.now_us:
            raise ValueError('不能倒退执行时间')
        while self.queue and self.queue[0][0] <= until_us:
            self.now_us, _, _, kind, payload = heapq.heappop(self.queue)
            self._dispatch(kind, payload)
        self.now_us = until_us
        return self.view()

    def run(self):
        while self.queue:
            self.run_until(self.queue[0][0])
        return self.result()

    def _dispatch(self, kind, payload):
        if kind == 'request':
            category, operation = payload
            self._rpc_cost(operation.kind if category == 'write' else 'observation', operation, 'request')
            self._log(kind, operation_id=operation.write_id if category == 'write' else operation.read_id)
        elif kind == 'apply':
            w = payload
            if w.success:
                gate = self.gates[w.node, w.flow]
                setattr(gate, w.field, w.value)
                setattr(gate, 'enabled_version' if w.field == 'enabled' else 'threshold_version', w.version)
            self._log(kind, write_id=w.write_id, node=w.node, flow=w.flow.label, field=w.field,
                      value=w.value, version=w.version, success=w.success)
        elif kind == 'ack':
            w = payload
            self._rpc_cost(w.kind, w, 'reply')
            self.confirmations.append(Confirmation(w.write_id, self.now_us, w.node, w.flow,
                                                   w.field, w.value, w.version, w.success))
            self._log(kind, write_id=w.write_id, success=w.success)
        elif kind == 'read_snapshot':
            r = payload
            self.read_values[r.read_id] = self.counters[r.flow]
            self._log(kind, read_id=r.read_id, count=self.counters[r.flow])
        elif kind == 'read_available':
            r = payload
            self._rpc_cost('observation', r, 'reply')
            if r.success:
                self.counts.append(CountObservation(r.read_id, r.flow, r.issued_us,
                                                    self.now_us, self.read_values[r.read_id]))
            self._log(kind, read_id=r.read_id, success=r.success)
        elif kind == 'ingress':
            self._ingress(*payload)
        elif kind == 'egress':
            self._egress(*payload)
        elif kind == 'sink':
            self._sink(payload)
        elif kind == 'report':
            self._report(*payload)

    def _ingress(self, packet_id, index):
        p, state = self.packets[packet_id], self.packet_state[packet_id]
        hop = p.hops[index]
        if state['length'] > hop.mtu:
            state['dropped'] = True
            self._log('ingress_drop', packet_id=packet_id, node=hop.object_id.node, ip_length=state['length'])
            return
        state['gate_times'][index] = self.nodes[hop.object_id.node].local(self.now_us)
        if index == 0:
            self.counters[p.flow] = (self.counters[p.flow] + 1) % (1 << 32)
            if p.ip_bytes + HEADER_BYTES <= hop.mtu:
                state['envelope'] = True
                state['length'] += HEADER_BYTES
        self._log('ingress', packet_id=packet_id, node=hop.object_id.node)
        self._schedule(hop.egress_us, 'egress', (packet_id, index))

    def _egress(self, packet_id, index):
        p, state = self.packets[packet_id], self.packet_state[packet_id]
        hop, records = p.hops[index], state['records']
        node = self.nodes[hop.object_id.node]
        gate = self.gates[node.node, p.flow]
        gate_time = state['gate_times'][index]
        elapsed = None if gate.last_local_us is None else (gate_time - gate.last_local_us) % (1 << node.clock_bits)
        if not state['envelope']:
            decision = 'no_envelope'
        elif not gate.enabled:
            decision = 'disabled'
        elif elapsed is not None and elapsed < gate.threshold_us:
            decision = 'time'
        elif len(records) >= self.capacity:
            decision = 'capacity'
        elif state['length'] + RECORD_BYTES > hop.mtu:
            decision = 'mtu'
        else:
            decision = 'insert'
            wire = WireRecord(node.node, index + 1, node.local(self.now_us), hop.queue_packets)
            records.insert(0, (wire, hop.object_id, self.now_us))
            state['length'] += RECORD_BYTES
            gate.last_local_us = gate_time
            self.insertions[node.node] += 1
        extra = state['length'] - p.ip_bytes
        self.inband[hop.link] += extra
        self.cost_events.append({'time_us': self.now_us, 'resource': 'inband', 'link': hop.link,
                                 'packet_id': packet_id, 'bytes': extra, 'basis': 'ipv4_increment'})
        self._log('egress', packet_id=packet_id, node=node.node, object_id=hop.object_id.label,
                  link=hop.link, decision=decision, ingress_local_us=gate_time, elapsed_us=elapsed,
                  threshold_us=gate.threshold_us, enabled=gate.enabled,
                  enabled_version=gate.enabled_version, threshold_version=gate.threshold_version,
                  extra_bytes=extra, count=len(records), last_local_us=gate.last_local_us)
        if index + 1 < len(p.hops):
            self._schedule(p.hops[index + 1].ingress_us, 'ingress', (packet_id, index + 1))
        else:
            self._schedule(p.sink_us, 'sink', packet_id)

    def _sink(self, packet_id):
        p, state = self.packets[packet_id], self.packet_state[packet_id]
        if not state['records'] or not p.export_emitted:
            self._log('sink', packet_id=packet_id, emitted=False)
            return
        payload = encode(p.flow, [row[0] for row in state['records']])
        raw, frame = 34 + len(payload), max(60, 34 + len(payload))
        export = {'packet_id': packet_id, 'time_us': self.now_us, 'payload_hex': payload.hex(),
                  'bytes_without_padding': raw, 'frame_bytes_without_fcs': frame}
        self.exports.append(export)
        self.cost_events.append({'time_us': self.now_us, 'resource': 'export', 'packet_id': packet_id,
                                 'bytes': frame, 'basis': 'ethernet_without_fcs_with_padding'})
        self._log('sink', packet_id=packet_id, emitted=True, export_bytes=frame)
        for arrived in p.report_arrivals_us:
            self._schedule(arrived, 'report', (packet_id, payload))

    def _report(self, packet_id, payload):
        flow, records = decode(payload)
        route, state = self.routes[flow], self.packet_state[packet_id]
        generated_truth = {row[0].hop: row[2] for row in state['records']}
        self.reports.append(ReceivedReport(self.now_us, flow, records))
        for record in records:
            object_id = route[record.hop - 1]
            if object_id.node != record.node:
                raise ValueError('报告节点与管理路径不一致')
            old = self.cache.get(object_id)
            modulus = 1 << self.nodes[record.node].clock_bits
            delta = None if old is None else (record.generated_local_us - old.generated_local_us) % modulus
            accepted = old is None or 0 < delta < modulus // 2
            if accepted:
                self.cache[object_id] = CacheState(object_id, record.generated_local_us, self.now_us, record.queue_packets)
            self.receipts.append({'object_id': object_id, 'generated_us': generated_truth[record.hop],
                                  'received_us': self.now_us, 'accepted': accepted})
        self._log('report', packet_id=packet_id, flow=flow.label, records=len(records))

    def view(self):
        if self.now_us is None:
            raise ValueError('先执行到一个明确时刻')
        return OnlineView(self.now_us, tuple(self.reports), tuple(self.counts), tuple(self.confirmations),
                          tuple(self.cache[o] for o in sorted(self.cache)))

    def result(self):
        control = {kind: sum(e['bytes'] for e in self.cost_events if e['resource'] == 'control' and e['kind'] == kind)
                   for kind in ('installation', 'observation', 'update')}
        writes = {kind: sum(w.kind == kind for w in self.writes if self.now_us is not None and w.issued_us <= self.now_us)
                  for kind in ('installation', 'update')}
        return {'events': list(self.events), 'cost_events': list(self.cost_events),
                'receipts': list(self.receipts), 'exports': list(self.exports),
                'selected': {p: [r[0].node for r in s['records']] for p, s in self.packet_state.items()},
                'dropped_packets': sorted(p for p, s in self.packet_state.items() if s['dropped']),
                'inband_per_link_bytes': dict(self.inband),
                'export_attempt_bytes': sum(e['bytes'] for e in self.cost_events if e['resource'] == 'export'),
                'received_report_bytes': sum(max(60, 34 + HEADER_BYTES + RECORD_BYTES * len(r.records)) for r in self.reports),
                'successful_insertions': dict(self.insertions),
                'control_serialized_bytes_by_kind': control,
                'issued_write_rpc_count_by_kind': writes,
                'allocated_gate_keys': len(self.gates), 'allocated_source_counter_keys': len(self.counters),
                'state_contract': '事件状态键数，不是 P4 静态数组释放、进程内存或硬件占用'}
