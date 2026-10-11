"""INTOPP期限与D-INTO稳定性核心及固定字段TT投影。"""
from dataclasses import dataclass
from itertools import product
import math

from orchestration.matched_grid import MatchedProxy
from orchestration.rolling import config_code, neighbors, shifted

METHODS = ('intopp_due', 'd_into_tt')


@dataclass(frozen=True)
class LiteratureSettings:
    value_scale: float = 144.0
    average_change_bytes: float = 12.0
    early_fraction: float = 0.0
    weighted_dependencies: bool = False

    def __post_init__(self):
        if not math.isfinite(self.value_scale) or self.value_scale <= 0:
            raise ValueError('收益系数必须正且有限')
        if not math.isfinite(self.average_change_bytes) or self.average_change_bytes < 0:
            raise ValueError('平均分配变化必须非负且有限')
        if not math.isfinite(self.early_fraction) or not 0 <= self.early_fraction <= 1:
            raise ValueError('提前期限比例须在[0,1]')
        if type(self.weighted_dependencies) is not bool:
            raise ValueError('依赖加权开关须为布尔值')


def assignment(config):
    return tuple(value is not None for value in config)


def change_bytes(previous, current, item_bytes=12):
    if len(previous) != len(current):
        raise ValueError('分配向量长度不同')
    return item_bytes * sum(a != b for a, b in zip(previous, current))


def unique_assignment(bits, keys):
    nodes = [node for enabled, (node, _) in zip(bits, keys) if enabled]
    return len(bits) == len(keys) and len(nodes) == len(set(nodes))


def native_rows(keys, benefits, previous, queue, value_scale, capacity_bytes, item_bytes=12):
    """完整枚举原生固定单字段P3，路径资格已由keys限定。"""
    if len(previous) != len(keys) or queue < 0 or not math.isfinite(queue) or value_scale <= 0:
        raise ValueError('原生求解状态非法')
    rows = []
    for bits in product((False, True), repeat=len(keys)):
        if not unique_assignment(bits, keys):
            continue
        if any(sum(item_bytes for enabled, (_, f) in zip(bits, keys) if enabled and f == flow) > cap
               for flow, cap in capacity_bytes.items()):
            continue
        info = sum(benefits.get(node, 0) for enabled, (node, _) in zip(bits, keys) if enabled)
        delta = change_bytes(previous, bits, item_bytes)
        rows.append(dict(assignment=bits, information=info, change_bytes=delta,
                         objective=queue * delta - value_scale * info))
    return rows


def dependency_due(recency_us, target_us, horizon_us, early_fraction=0):
    return recency_us is None or recency_us + early_fraction * horizon_us > target_us


class LiteratureProxy(MatchedProxy):
    def __init__(self, *args, method, literature, queue, **kwargs):
        super().__init__(*args, **kwargs)
        self.method, self.literature, self.queue = method, literature, queue

    def evaluate(self, config):
        row = super().evaluate(config)
        if 'literature_objective' in row:
            return row
        # 仅遍历由合法观察生成的预测包。容量阻挡不会推进门控。
        gates, samples = dict(self.last_estimate), dict.fromkeys((d.object_id.node for d in self.demands), 0)
        policy = dict(zip(self.management.keys, config))
        choices = []
        for packet in sorted(self.packets, key=lambda p: (p.hops[0].ingress_us, p.packet_id)):
            chosen = []
            for hop in packet.hops:
                key = hop.object_id.node, packet.flow
                threshold = policy[key]
                if threshold is None or len(chosen) >= self.settings.capacity:
                    continue
                if key in gates and (hop.ingress_us - gates[key]) % (1 << 48) < threshold:
                    continue
                gates[key] = hop.ingress_us
                chosen.append(hop.object_id.node)
                if packet.report_arrivals_us[0] < self.view.now_us + self.settings.horizon_us:
                    if hop.object_id.node in samples:
                        samples[hop.object_id.node] += 1
            choices.append({'packet_id': packet.packet_id, 'nodes': chosen})
        info, dependencies, inputs = 0.0, 0.0, []
        for demand in self.demands:
            node = demand.object_id.node
            recency = self.view.effective_recency_us(demand.object_id)
            due = dependency_due(recency, demand.target_us, self.settings.horizon_us, self.literature.early_fraction)
            attained = min(1.0, samples[node] * demand.target_us / self.settings.horizon_us)
            info += demand.weight * attained
            dependencies += (demand.weight if self.literature.weighted_dependencies else 1) * (due and samples[node] > 0)
            inputs.append(dict(node=node, weight=demand.weight, target_us=demand.target_us,
                               recency_us=recency, due=due, predicted_samples=samples[node], attained=attained))
        delta = change_bytes(assignment(self.current), assignment(config))
        objective = self.queue * delta - self.literature.value_scale * info if self.method == 'd_into_tt' else -dependencies
        row.update(literature_objective=objective, information=info, fulfilled_dependencies=dependencies,
                   assignment_change_bytes=delta, assignment_admissible=unique_assignment(assignment(config), self.management.keys),
                   literature_inputs=inputs, predicted_packet_choices=choices)
        return row

    def rank(self, row):
        return (round(row['predicted_excess'], 12), row['literature_objective'], row['update_rpc_count'], config_code(row['config']))


def select_literature(proxy, trained):
    if not proxy.ready or not proxy.demands:
        return dict(config=proxy.current, reason='insufficient_observations', evaluated_candidates=0, forecast=None)
    current = proxy.evaluate(proxy.current)
    selected = current if current['assignment_admissible'] else proxy.evaluate((None,) * len(proxy.current))
    pool = [trained, shifted(proxy.current, -1, proxy.settings), shifted(proxy.current, 1, proxy.settings)]
    # 原生唯一分配的所有许可掩码提供起点，阈值仍取共同网格。
    for bits in product((False, True), repeat=len(proxy.current)):
        if unique_assignment(bits, proxy.management.keys):
            pool.append(tuple(proxy.settings.grid_us[0] if b else None for b in bits))
    while len(proxy.memo) < proxy.settings.max_candidates:
        before = selected['config']
        candidates, pool = pool + list(neighbors(selected['config'], proxy.settings)), []
        for config in dict.fromkeys(candidates):
            if not unique_assignment(assignment(config), proxy.management.keys):
                continue
            if config not in proxy.memo and len(proxy.memo) >= proxy.settings.max_candidates:
                break
            row = proxy.evaluate(config)
            if row['update_allowed'] and proxy.rank(row) < proxy.rank(selected):
                selected = row
        if selected['config'] == before:
            break
    return dict(config=selected['config'], reason=proxy.method, evaluated_candidates=len(proxy.memo), forecast=selected)
