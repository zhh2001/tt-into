"""共同网格真实主循环的自有费用与合法观察策略入口。"""
from orchestration.adaptive import history_entry, select
from orchestration.control_ledger import ControlLedger
from orchestration.matched_grid import ControlCaps, METHODS, MatchedProxy, settings
from orchestration.rolling import changes, choose


KINDS = ('installation', 'cold_start_reset', 'online_observation', 'update', 'reconciliation', 'controlled_failed_read')


def legitimate_rpc(row):
    register = row.get('arguments', [''])[0]
    return row['kind'] in KINDS and row['method'] != 'bm_register_read_all' and not (
        isinstance(register, str) and register.startswith(('TTIngress.trace_', 'TTEgress.trace_')))


class OwnControl:
    def __init__(self, rpc_log, origin_ns, start_index=0):
        self.log, self.origin_ns, self.cursor = rpc_log, origin_ns, start_index
        self.ledger = ControlLedger()

    def sync(self):
        if self.cursor > len(self.log):
            raise ValueError('自有调用日志发生截断')
        for i in range(self.cursor, len(self.log)):
            if legitimate_rpc(self.log[i]):
                self.ledger.completed_rpc(f'rpc-{i}', self.log[i], self.origin_ns)
        self.cursor = len(self.log)

    def notice(self, operation_id, payload, received_ns):
        self.ledger.received_notice(operation_id, payload, received_ns, self.origin_ns)

    def snapshot(self, cutoff_ns):
        self.sync()
        return self.ledger.snapshot(cutoff_ns - self.origin_ns)


class LivePlanner:
    def __init__(self, method, management, budget, adaptive, trained):
        if method not in METHODS:
            raise ValueError('未知共同网格方法')
        self.method, self.management, self.budget = method, management, budget
        self.adaptive, self.trained, self.options = adaptive, tuple(trained), settings()
        self.caps = ControlCaps(budget.control_bytes_per_s, 128)
        self.history = []

    def decide(self, view, control):
        temporal = self.method in ('phase_joint', 'phase_guard', 'phase_local_guard')
        guarded = self.method in ('uniform_guard', 'phase_guard', 'phase_local_guard')
        proxy = MatchedProxy(view, self.management, self.budget, self.options, self.adaptive,
                             control, self.caps, tuple(self.history), temporal, guarded)
        selected = choose(proxy, self.method, self.trained) if self.method in METHODS[:4] else select(
            proxy, self.trained, self.method == 'phase_local_guard')
        planned = changes(selected['config'], proxy.fields, self.management)
        if len(proxy.memo) > 48 or len(planned) > 6 or self.method == 'static' and planned:
            raise ValueError('候选、写限额或静态保持合同错误')
        result = {'selected': selected, 'current_config': proxy.current, 'budget_history': tuple(self.history),
                  'budget_diagnostic': proxy.budget_diagnostic, 'candidate_scores': list(proxy.memo.values()),
                  'rates': [{'flow': f, 'rate_pps': proxy.rates.get(f)} for f in sorted(self.management.routes)],
                  'profiles': proxy.profiles}
        entry = history_entry(proxy, selected)
        if entry is not None:
            self.history.append(entry)
        return result, planned
