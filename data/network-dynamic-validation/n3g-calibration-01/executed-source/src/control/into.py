"""独立动态接口：异常计费、逐字段确认、延迟确认和付费状态核对。"""
import time

from control.metered import MeteredClient
from orchestration.event_observation import Confirmation

REGISTERS = {'enabled': 'TTEgress.max_count', 'threshold_us': 'TTEgress.time_threshold'}


def clean(value):
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, '__dict__'):
        return {k: clean(v) for k, v in value.__dict__.items()}
    return value


class IntoClient(MeteredClient):
    def __init__(self, port, node, log):
        super().__init__(port, node, log)
        self.socket.setTimeout(1500)

    def call(self, method, *args, kind='installation'):
        before = time.monotonic_ns()
        sent, received = self.socket.sent, self.socket.received
        result, error = None, None
        try:
            result = getattr(self.api, method)(0, *args)
            return result
        except Exception as exc:
            error = type(exc).__name__ + ': ' + str(exc)
            raise
        finally:
            self.log.append({'node': self.node, 'method': method, 'arguments': clean(args),
                             'before_monotonic_ns': before, 'after_monotonic_ns': time.monotonic_ns(),
                             'kind': kind, 'success': error is None, 'error': error, 'result': clean(result),
                             'request_serialized_bytes': self.socket.sent - sent,
                             'reply_serialized_bytes': self.socket.received - received})

    def register_flow(self, sport, slot, tag, session):
        if not 0 <= slot < 256 or not 0 <= tag < 2**32 or not 0 <= session < 2**32:
            raise ValueError('身份或流槽越界')
        fields = ((0x0a000001, 32), (0x0a000002, 32), (17, 8), (sport, 16), (9000, 16))
        match = [self.types.BmMatchParam(type=self.types.BmMatchParamType.EXACT,
                 exact=self.types.BmMatchParamExact(self.encode([field])[0])) for field in fields]
        return self.call('bm_mt_add_entry', 'TTIngress.flow_map', match, 'TTIngress.assign_flow',
                         self.encode([(slot, 32), (tag, 32), (session, 32)]), self.types.BmAddEntryOptions())


class ConfirmedFields:
    """软件序列号只用于本地确认排序，不代表数据面 CAS 或原子事务。"""
    def __init__(self, clients, slots, origin_ns):
        self.clients, self.slots, self.origin_ns = clients, slots, origin_ns
        self.versions, self.values, self.pending = {}, {}, {}
        self.confirmations, self.actions = [], []

    def unresolved(self):
        return tuple(key for key in self.versions if key not in self.values or key in self.pending)

    def poll(self, now_ns):
        for key, entry in list(self.pending.items()):
            if entry['available_ns'] is not None and entry['available_ns'] <= now_ns:
                self.confirmations.append(Confirmation(entry['id'], (now_ns - self.origin_ns) // 1000,
                                          key[0], key[1], key[2], entry['value'], entry['version'], True))
                next(row for row in self.actions if row['id'] == entry['id'])['confirmation_available_ns'] = now_ns
                self.values[key] = entry['value']
                del self.pending[key]

    def write(self, node, flow, field, value, *, delay_ns=0, bad_register=False, kind='update'):
        if field not in REGISTERS or field == 'enabled' and type(value) is not bool:
            raise ValueError('配置字段或许可值错误')
        if field == 'threshold_us' and (type(value) is not int or not 1 <= value < 2**47):
            raise ValueError('时间阈值越界')
        key = node, flow, field
        if key in self.pending:
            raise ValueError('同字段有未解决调用')
        version = self.versions.get(key, 0) + 1
        self.versions[key] = version
        action_id = f'{node}-{flow.label}-{field}-{version}'
        name = 'TTEgress.missing_for_fault' if bad_register else REGISTERS[field]
        begin = len(self.clients[node].log)
        error = None
        try:
            self.clients[node].write(name, self.slots[flow], int(value), kind=kind)
        except Exception as exc:
            error = type(exc).__name__ + ': ' + str(exc)
        rpc = self.clients[node].log[begin]
        available = None if error else rpc['after_monotonic_ns'] + delay_ns
        self.pending[key] = {'available_ns': available, 'value': value, 'version': version, 'id': action_id}
        row = {'id': action_id, 'node': node, 'flow': flow, 'field': field, 'value': value, 'version': version,
               'rpc_index': begin, 'success': error is None, 'error': error, 'confirmation_available_ns': None,
               'confirmation_not_before_ns': available,
               'fault': 'controlled_missing_register' if bad_register else None,
               'confirmation_delay_ns': delay_ns, 'effect_scope': '真实生效位于请求与回复之间，未假定多节点原子'}
        self.actions.append(row)
        self.poll(time.monotonic_ns())
        return row

    def reconcile(self):
        """失败后的实际值通过付费读取确认，不猜测未应用的字段。"""
        results = []
        for key, pending in list(self.pending.items()):
            if pending['available_ns'] is not None:
                continue
            node, flow, field = key
            value = self.clients[node].read(REGISTERS[field], self.slots[flow], kind='reconciliation')
            value = bool(value) if field == 'enabled' else value
            version = self.versions[key] + 1
            self.versions[key] = version
            now = time.monotonic_ns()
            self.confirmations.append(Confirmation('reconcile-' + pending['id'], (now - self.origin_ns) // 1000,
                                      node, flow, field, value, version, True))
            self.values[key] = value
            del self.pending[key]
            results.append({'node': node, 'flow': flow, 'field': field, 'value': value, 'received_ns': now})
        return results
