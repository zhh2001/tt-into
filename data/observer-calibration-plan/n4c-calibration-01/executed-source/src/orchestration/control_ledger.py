"""自有调用与实收消息的可用前缀，序列化字节分层计费。"""
from dataclasses import dataclass
import json

from orchestration.event_schema import integer

API = 'api_serialized'
NOTICE = 'local_notice_payload'
LAYERS = (API, NOTICE)


def notice_payload(message):
    """与现有 UNIX 发送接口采用相同编码，不假设固定通知长度。"""
    return json.dumps(message, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


@dataclass(frozen=True)
class Charge:
    operation_id: str
    layer: str
    kind: str
    part: str
    time_ns: int
    known_ns: int
    bytes: int
    success: bool
    provenance: str

    def __post_init__(self):
        integer(self.time_ns, '费用归因时刻')
        integer(self.known_ns, '费用完整已知时刻')
        integer(self.bytes, '费用字节', 0)
        if self.layer not in LAYERS or not self.operation_id or not self.kind or not self.provenance:
            raise ValueError('费用身份、层或来源错误')
        if self.time_ns > self.known_ns or type(self.success) is not bool:
            raise ValueError('费用可用时刻或调用结果错误')
        if self.part not in (('request', 'reply') if self.layer == API else ('payload',)):
            raise ValueError('费用层与部分不匹配')


@dataclass(frozen=True)
class ControlSnapshot:
    cutoff_ns: int
    charges: tuple[Charge, ...]

    def __post_init__(self):
        integer(self.cutoff_ns, '费用视图截止时间')
        keys = [(r.operation_id, r.layer, r.part) for r in self.charges]
        if len(keys) != len(set(keys)) or any(r.known_ns > self.cutoff_ns for r in self.charges):
            raise ValueError('费用视图含重复或未来已知项')

    def window(self, start_ns, end_ns):
        integer(start_ns, '费用窗口起点')
        integer(end_ns, '费用窗口终点')
        if start_ns > end_ns:
            raise ValueError('费用窗口倒序')
        totals = {layer: 0 for layer in LAYERS}
        by_kind = {layer: {} for layer in LAYERS}
        for r in self.charges:
            if start_ns <= r.time_ns < end_ns:
                totals[r.layer] += r.bytes
                by_kind[r.layer][r.kind] = by_kind[r.layer].get(r.kind, 0) + r.bytes
        return {'window_ns': [start_ns, end_ns], 'bytes_by_layer': totals, 'bytes_by_layer_and_kind': by_kind}


class ControlLedger:
    """只接收自己已完成的调用及实收消息，不读取执行器或链路账本。"""
    def __init__(self):
        self._charges = {}

    def add(self, charge):
        key = charge.operation_id, charge.layer, charge.part
        old = self._charges.get(key)
        if old is not None and old != charge:
            raise ValueError('相同费用身份出现冲突')
        self._charges[key] = charge

    def completed_rpc(self, operation_id, row, origin_ns=0, provenance=None):
        """归因使用调用边界，完整长度最早在返回后可用。"""
        before, after = row['before_monotonic_ns'] - origin_ns, row['after_monotonic_ns'] - origin_ns
        if before > after:
            raise ValueError('调用边界倒序')
        charges = tuple(Charge(operation_id, API, row['kind'], part, stamp, after,
                               row[key], row['success'], provenance or '自有调用实际传输计数；调用边界归因，非精确帧发送时刻')
                        for part, stamp, key in (('request', before, 'request_serialized_bytes'),
                                                ('reply', after, 'reply_serialized_bytes')))
        for charge in charges:
            self.add(charge)

    def received_notice(self, operation_id, payload, received_ns, origin_ns=0, provenance=None):
        if not isinstance(payload, bytes):
            raise ValueError('通知需提供实际收到的字节')
        stamp = received_ns - origin_ns
        self.add(Charge(operation_id, NOTICE, 'management_notice', 'payload', stamp, stamp,
                        len(payload), True, provenance or '实收本地 UNIX 数据报有效负载，不含网络帧费用'))

    def snapshot(self, cutoff_ns):
        return ControlSnapshot(cutoff_ns, tuple(sorted((r for r in self._charges.values() if r.known_ns <= cutoff_ns),
                               key=lambda r: (r.time_ns, r.operation_id, r.part))))
