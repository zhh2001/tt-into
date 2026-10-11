"""报告观察与编排决策的共同单调时钟槽，不承诺硬完成期限。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ObserverSchedule:
    origin_ns: int
    observation_us: int = 100000
    decision_us: int = 1000000
    duration_us: int = 10000000

    def __post_init__(self):
        if any(type(v) is not int for v in (self.origin_ns, self.observation_us, self.decision_us, self.duration_us)):
            raise ValueError('调度参数必须为整数')
        if self.origin_ns < 0 or min(self.observation_us, self.decision_us, self.duration_us) <= 0 or \
                self.decision_us % self.observation_us or self.duration_us % self.decision_us:
            raise ValueError('调度周期与运行范围不相容')

    @property
    def slots(self):
        return self.duration_us // self.observation_us

    def slot(self, index, began_ns):
        if not 0 <= index < self.slots:
            raise ValueError('报告槽越界')
        offset = index * self.observation_us
        scheduled = self.origin_ns + offset * 1000
        if began_ns < scheduled:
            raise ValueError('实际执行早于计划槽')
        return {'observation_index': index, 'scheduled_offset_us': offset, 'scheduled_ns': scheduled,
                'cycle_started_ns': began_ns, 'observation_skipped': began_ns >= scheduled + self.observation_us * 1000,
                'decision_due': offset % self.decision_us == 0,
                'decision_skipped': began_ns >= scheduled + self.decision_us * 1000,
                'lateness_ns': began_ns - scheduled}
