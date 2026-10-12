"""文献TT适配的独立真实入口，虚拟队列用已确认许可结算。"""
from orchestration.adaptive import history_entry
from orchestration.literature import METHODS, LiteratureProxy, LiteratureSettings, assignment, change_bytes, select_literature
from orchestration.matched_grid import ControlCaps, settings
from orchestration.rolling import changes


class LiteraturePlanner:
    def __init__(self, method, management, budget, adaptive, trained, literature=None):
        if method not in METHODS:
            raise ValueError('未知文献适配')
        self.method, self.management, self.budget = method, management, budget
        self.adaptive, self.trained, self.options = adaptive, tuple(trained), settings()
        self.literature = literature or LiteratureSettings()
        self.caps = ControlCaps(budget.control_bytes_per_s, 128)
        self.history, self.queue, self.previous_assignment = [], 0.0, None

    def decide(self, view, control):
        proxy = LiteratureProxy(view, self.management, self.budget, self.options, self.adaptive, control, self.caps,
            tuple(self.history), temporal=False, guarded=False, method=self.method, literature=self.literature, queue=self.queue)
        current = assignment(proxy.current)
        previous = self.previous_assignment
        delta = 0 if self.previous_assignment is None else change_bytes(self.previous_assignment, current)
        before = self.queue
        if self.method == 'd_into_tt':
            self.queue = max(0, self.queue + delta - self.literature.average_change_bytes)
        self.previous_assignment = current
        proxy.queue = self.queue
        selected = select_literature(proxy, self.trained)
        planned = changes(selected['config'], proxy.fields, self.management)
        if len(proxy.memo) > 48 or len(planned) > 6:
            raise ValueError('文献适配共同资源限额错误')
        result = dict(selected=selected, current_config=proxy.current, budget_history=tuple(self.history),
            budget_diagnostic=proxy.budget_diagnostic, candidate_scores=list(proxy.memo.values()),
            rates=[dict(flow=f, rate_pps=proxy.rates.get(f)) for f in sorted(self.management.routes)], profiles=proxy.profiles,
            literature_state=dict(method=self.method, parameters=self.literature, queue_before=before, queue_after=self.queue,
                previous_confirmed_assignment=previous, current_confirmed_assignment=current,
                settled_change_bytes=delta, basis='仅相邻已确认许可变化'))
        entry = history_entry(proxy, selected)
        if entry is not None:
            self.history.append(entry)
        return result, planned
