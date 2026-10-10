"""仅含实收报告、已付费统计和确认历史的在线视图。"""
from dataclasses import dataclass

from collector.tagged_codec import WireRecord
from orchestration.event_schema import Demand, FlowId, ObjectId, integer


@dataclass(frozen=True)
class ReceivedReport:
    received_us: int
    flow: FlowId
    records: tuple[WireRecord, ...]


@dataclass(frozen=True)
class CountObservation:
    read_id: str
    flow: FlowId
    issued_us: int
    available_us: int
    count: int


@dataclass(frozen=True)
class Confirmation:
    write_id: str
    received_us: int
    node: int
    flow: FlowId
    field: str
    value: int | bool
    version: int
    success: bool


@dataclass(frozen=True)
class CacheState:
    object_id: ObjectId
    generated_local_us: int
    received_us: int
    queue_packets: int


@dataclass(frozen=True)
class DemandNotification:
    received_us: int
    version: int
    demands: tuple[Demand, ...]


@dataclass(frozen=True)
class OnlineView:
    now_us: int
    reports: tuple[ReceivedReport, ...]
    counts: tuple[CountObservation, ...]
    confirmations: tuple[Confirmation, ...]
    cache: tuple[CacheState, ...]
    notifications: tuple[DemandNotification, ...] = ()

    def effective_recency_us(self, object_id):
        found = next((entry for entry in self.cache if entry.object_id == object_id), None)
        return None if found is None else self.now_us - found.received_us


@dataclass(frozen=True)
class RateEstimate:
    flow: FlowId
    observed_until_us: int
    rate_pps: float | None
    observation_count: int


def estimate_rates(view, alpha=0.5):
    """响应到达间隔上的计数差分 EWMA，不声称恢复精确到包过程。"""
    if not 0 < alpha <= 1:
        raise ValueError('EWMA 系数须在 (0,1]')
    previous, rates, observations = {}, {}, {}
    for row in view.counts:
        integer(row.count, '到包计数', 0, 2**32 - 1)
        if row.available_us > view.now_us:
            raise ValueError('在线视图包含未来统计')
        old = previous.get(row.flow)
        if old is not None:
            duration = row.available_us - old.available_us
            if duration < 0:
                raise ValueError('统计未按响应时间排序')
            delta = (row.count - old.count) % (1 << 32)
            if duration > 0 and delta < 2**31:
                rate = delta * 1_000_000 / duration
                rates[row.flow] = rate if rates.get(row.flow) is None else alpha * rate + (1 - alpha) * rates[row.flow]
            elif delta >= 2**31:
                rates[row.flow] = None
        previous[row.flow] = row
        observations[row.flow] = observations.get(row.flow, 0) + 1
    return tuple(RateEstimate(flow, previous[flow].available_us, rates.get(flow), observations[flow])
                 for flow in sorted(previous))
