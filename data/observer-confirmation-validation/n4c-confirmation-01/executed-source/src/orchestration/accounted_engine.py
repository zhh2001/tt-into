"""保留 TT-INT 包事件，独立接入消息层和自有调用完成费用。"""
from dataclasses import dataclass

from orchestration.control_ledger import ControlLedger, notice_payload
from orchestration.event_engine import EventEngine
from orchestration.event_metrics import window_costs
from orchestration.event_observation import DemandNotification
from orchestration.event_schema import integer
from orchestration.rolling import excess


@dataclass(frozen=True)
class ManagedNotice:
    notice_id: str
    released_us: int
    available_us: int
    version: int
    demands: tuple
    payload: bytes

    def __post_init__(self):
        for stamp in (self.released_us, self.available_us, self.version):
            integer(stamp, '管理消息时刻或版本', 0)
        if self.available_us < self.released_us or not self.notice_id or not self.demands:
            raise ValueError('管理消息因果关系或内容错误')
        expected = {'version': self.version, 'demands': [{'node': d.object_id.node, 'target_us': d.target_us,
                     'weight': d.weight, 'active': d.active} for d in self.demands]}
        if self.payload != notice_payload(expected) or len({d.object_id for d in self.demands}) != len(self.demands):
            raise ValueError('管理消息编码或需求重复错误')
        if any(d.object_id.port != 2 or d.object_id.queue != 0 for d in self.demands):
            raise ValueError('当前消息接口固定出口 2、默认队列')


def managed_notice(notice, epoch_us=1000000):
    integer(epoch_us, '管理轮询周期', 1)
    message = {'version': notice.version, 'demands': [{'node': d.object_id.node, 'target_us': d.target_us,
               'weight': d.weight, 'active': d.active} for d in notice.demands]}
    # 1 us 合成消息延迟后，在下一次控制轮询提供给策略。
    at = ((notice.available_us + 1 + epoch_us - 1) // epoch_us) * epoch_us
    return ManagedNotice(notice.notice_id, notice.available_us, at, notice.version, notice.demands, notice_payload(message))


class AccountedEngine(EventEngine):
    def __init__(self, nodes, routes, packets, writes=(), reads=(), capacity=1, notices=()):
        self.control_ledger = ControlLedger()
        self.completed_calls, self.notice_deliveries = [], []
        super().__init__(nodes, routes, packets, writes, reads, capacity)
        objects = {o for route in routes.values() for o in route}
        if len({n.notice_id for n in notices}) != len(notices):
            raise ValueError('管理消息身份重复')
        for notice in notices:
            if any(d.object_id not in objects for d in notice.demands):
                raise ValueError('消息对象未登记')
            self._schedule(notice.available_us, 'notice', notice)

    def _dispatch(self, kind, payload):
        if kind == 'notice':
            n = payload
            self.notifications.append(DemandNotification(self.now_us, n.version, n.demands))
            self.cost_events.append({'time_us': self.now_us, 'resource': 'local_notice', 'kind': 'management_notice',
                'operation_id': n.notice_id, 'part': 'payload', 'bytes': len(n.payload), 'basis': 'unix_datagram_payload',
                'provenance': '合成送达事件按实际接口编码长度计费，不是本轮网络传输'})
            self.control_ledger.received_notice(n.notice_id, n.payload, self.now_us * 1000,
                provenance='合成管理消息可用事件；长度为实际接口编码，非本轮网络传输')
            self.notice_deliveries.append({'notice_id': n.notice_id, 'available_us': self.now_us,
                                          'payload_hex': n.payload.hex(), 'payload_bytes': len(n.payload)})
            self._log('notice', notice_id=n.notice_id, version=n.version)
            return
        super()._dispatch(kind, payload)
        if kind in ('ack', 'read_available'):
            op = payload
            is_write = kind == 'ack'
            operation_id = op.write_id if is_write else op.read_id
            row = {'operation_id': operation_id, 'kind': op.kind if is_write else 'observation',
                'before_monotonic_ns': op.issued_us * 1000, 'after_monotonic_ns': self.now_us * 1000,
                'request_serialized_bytes': op.cost.request_bytes, 'reply_serialized_bytes': op.cost.reply_bytes,
                'success': op.success}
            self.completed_calls.append(row)
            self.control_ledger.completed_rpc(operation_id, row,
                provenance='自有合成调用完成事件；长度引用费用参照，不是本轮真实 RPC')

    def control_snapshot(self):
        return self.control_ledger.snapshot(self.now_us * 1000)

    def result(self):
        result = super().result()
        result.update(completed_calls=list(self.completed_calls), notice_deliveries=list(self.notice_deliveries),
                      local_notice_payload_bytes=sum(e['bytes'] for e in self.cost_events if e['resource'] == 'local_notice'))
        return result


def layered_costs(events, start_us, end_us, link_budgets=None, export_budget=None):
    local = []
    base = []
    for e in events:
        if e['resource'] == 'local_notice':
            if e.get('basis') != 'unix_datagram_payload' or e.get('part') != 'payload':
                raise ValueError('本地消息费用口径错误')
            integer(e['bytes'], '消息字节', 0)
            integer(e['time_us'], '消息可用时刻')
            local.append(e)
        else:
            base.append(e)
    row = window_costs(base, start_us, end_us, link_budgets, export_budget)
    amount = sum(e['bytes'] for e in local if start_us <= e['time_us'] < end_us)
    row.update(local_notice_payload_bytes=amount, local_notice_payload_bytes_per_s=amount / ((end_us - start_us) / 1e6),
               budget_contract='API、UNIX 有效负载、带内和导出分层；平均窗口，非硬监管')
    return row


def layered_excess(cost, budget, caps):
    if budget.control_bytes_per_s != caps.api_bytes_per_s:
        raise ValueError('API 预算接口不一致')
    rate, cap = cost['local_notice_payload_bytes_per_s'], caps.notice_payload_bytes_per_s
    return excess(cost, budget) + (max(0, rate / cap - 1) if cap else rate)
