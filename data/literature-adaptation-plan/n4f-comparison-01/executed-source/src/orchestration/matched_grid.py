"""共同配置集合与已知控制费用适配，不改变历史模型源码。"""
from dataclasses import dataclass
import math

from orchestration.adaptive import AdaptiveProxy
from orchestration.control_ledger import API, NOTICE
from orchestration.rolling import Budget, Settings, changes, excess

GRID_US = (50000, 100000, 150000, 200000, 400000, 600000, 800000, 1000000)
METHODS = ('static', 'feedback', 'local', 'joint', 'phase_joint', 'uniform_guard', 'phase_guard', 'phase_local_guard')


def settings():
    return Settings(grid_us=GRID_US, max_candidates=48, max_update_rpcs=6)


def validate_config(config, management):
    if len(config) != len(management.keys) or any(v is not None and (type(v) is not int or v not in GRID_US) for v in config):
        raise ValueError('配置不属于共同集合')
    return tuple(config)


@dataclass(frozen=True)
class ControlCaps:
    api_bytes_per_s: float
    notice_payload_bytes_per_s: float

    def __post_init__(self):
        if any(isinstance(v, bool) or not math.isfinite(v) or v < 0 for v in
               (self.api_bytes_per_s, self.notice_payload_bytes_per_s)):
            raise ValueError('控制分层预算须有限且非负')


class MatchedProxy(AdaptiveProxy):
    """数据费用仍是因果估计；控制历史使用已知的自有费用。"""
    def __init__(self, view, management, budget, settings, adaptive, control, caps, history=(), temporal=True, guarded=True):
        if settings.grid_us != GRID_US:
            raise ValueError('静态与动态必须使用共同八档网格')
        if control.cutoff_ns // 1000 != view.now_us:
            raise ValueError('费用前缀与在线视图截止时间不一致')
        if budget.control_bytes_per_s != caps.api_bytes_per_s:
            raise ValueError('旧接口控制预算必须对应 API 层')
        self.control, self.control_caps = control, caps
        super().__init__(view, management, budget, settings, adaptive, history, temporal, guarded)
        validate_config(self.current, management)
        start, end = adaptive.measurement_start_us * 1000, adaptive.measurement_end_us * 1000
        # 当前时刻已经可用的项属于已花费用，测量右边界仍排除。
        spent = control.window(start, min(end, max(start, control.cutoff_ns + 1)))
        amounts = spent['bytes_by_layer']
        duration = (end - start) / 1e9
        remaining = self.budget_diagnostic['remaining_s']
        remaining_api = max(0.0, (duration * caps.api_bytes_per_s - amounts[API]) / remaining) * (1 - adaptive.reserve_fraction)
        self.effective_budget = Budget(self.effective_budget.link_bytes_per_s,
                                       self.effective_budget.export_bytes_per_s, remaining_api)
        self.notice_debt = max(0.0, amounts[NOTICE] / (duration * caps.notice_payload_bytes_per_s) - 1) \
            if caps.notice_payload_bytes_per_s else float(amounts[NOTICE])
        spend = self.budget_diagnostic['spend']
        spend['unused_legacy_control_prediction_bytes'] = spend.pop('control_bytes')
        spend['control_bytes'] = amounts[API]
        spend['notice_payload_bytes'] = amounts[NOTICE]
        self.budget_diagnostic.update(control_ledger_window=spent, effective_budget=self.effective_budget,
                                      control_caps=caps, known_notice_debt=self.notice_debt,
                                      control_scope='API 与 UNIX 有效负载分别核对；未返回调用的完整实际长度不提前可用')

    def evaluate(self, config):
        config = validate_config(config, self.management)
        row = super().evaluate(config)
        if 'control_forecast_source' in row:
            return row
        diffs = changes(config, self.fields, self.management)
        read = self.management.read_cost or self.management.rpc_cost
        seconds = self.settings.horizon_us / 1e6
        # 已收到通知在历史层记费，下一周期通知的实际长度并不提前已知。
        row['cost']['control_serialized_bytes_by_kind'] = {
            'installation': 0,
            'observation': len(self.management.routes) * (read.request_bytes + read.reply_bytes),
            'update': sum(self.management.cost_for_write(field).request_bytes + self.management.cost_for_write(field).reply_bytes
                          for _, field, _ in diffs)}
        row['cost']['control_request_count_by_kind'] = {
            'installation': 0, 'observation': len(self.management.routes), 'update': len(diffs)}
        row['cost']['updated_entry_count'] = len({key for key, _, _ in diffs})
        row['predicted_excess'] = excess(row['cost'], self.budget) + self.notice_debt
        row['effective_budget_excess'] = excess(row['cost'], self.effective_budget) + self.notice_debt
        row['control_forecast_source'] = '下一周期计划源计数与字段写，失败长度未预知；已收通知不重复收费'
        row['predicted_api_bytes_per_s'] = sum(row['cost']['control_serialized_bytes_by_kind'].values()) / seconds
        row['known_notice_debt'] = self.notice_debt
        return row
