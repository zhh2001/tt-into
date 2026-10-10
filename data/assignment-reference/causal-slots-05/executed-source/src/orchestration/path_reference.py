"""固定分支路径的事件参照。动态配置沿用 TT-INT 调阈值，不作为新算法。"""
from dataclasses import dataclass
from itertools import product
import math
import random

from orchestration.budget_model import FORMATS
from orchestration.metrics import age_metrics
from orchestration.model import Task


@dataclass(frozen=True)
class Opportunity:
    packet_id: int
    flow: int
    path: tuple[int, ...]
    links: tuple[int, ...]
    gate_us: tuple[float, ...]
    generated_us: tuple[float, ...]
    arrival_us: float | None
    ip_bytes: int = 120
    capacity: int = 1
    export_emitted: bool = True


@dataclass(frozen=True)
class Phase:
    start_us: float
    end_us: float
    deadlines_us: tuple[float, ...]


@dataclass(frozen=True)
class Policy:
    # flow scope: (node, flow)。port scope: (node, 0)。None 用原有 capacity=0 禁用。
    thresholds: dict
    scope: str = 'flow'
    ratios: tuple[int, ...] = (1, 1, 1)
    counter_offsets: tuple[int, ...] = (0, 0, 0)
    selection: str = 'prefix'
    weights: tuple[int, ...] = (1, 1, 1)
    format_name: str = 'prefix'


def validate(packets, phases, policies):
    if not packets or not phases or not policies:
        raise ValueError('轨迹、需求和配置不能为空')
    ids = set()
    last = -math.inf
    for p in packets:
        if p.packet_id in ids or p.flow not in (1, 2, 3):
            raise ValueError('包标识重复或流不存在')
        ids.add(p.packet_id)
        if not p.path or len(set(p.path)) != len(p.path) or not set(p.path) <= {1, 2, 3}:
            raise ValueError('物理对象路径不合法')
        if len(p.links) != len(p.path) or len(p.gate_us) != len(p.path) or len(p.generated_us) != len(p.path):
            raise ValueError('路径、链路与时间维度不一致')
        if not set(p.links) <= {0, 1, 2} or len(set(p.links)) != len(p.links):
            raise ValueError('物理链路不合法')
        times = p.gate_us + p.generated_us
        if not all(math.isfinite(t) for t in times) or p.gate_us[0] < last:
            raise ValueError('包事件非有限或源端乱序')
        last = p.gate_us[0]
        if any(g < a for g, a in zip(p.generated_us, p.gate_us)) or any(b < a for a, b in zip(p.generated_us[:-1], p.gate_us[1:])):
            raise ValueError('节点事件因果关系错误')
        if p.arrival_us is not None and (not math.isfinite(p.arrival_us) or p.arrival_us < p.generated_us[-1]):
            raise ValueError('收报早于生成')
        if not p.export_emitted and p.arrival_us is not None:
            raise ValueError('不存在的导出不能送达')
        if not isinstance(p.capacity, int) or not 1 <= p.capacity <= 8 or not 28 <= p.ip_bytes <= 1500:
            raise ValueError('项数上限须在 1..8 及 IPv4 MTU 范围内')
    for i, phase in enumerate(phases):
        if not all(math.isfinite(t) for t in (phase.start_us, phase.end_us, *phase.deadlines_us)) or phase.start_us >= phase.end_us:
            raise ValueError('需求窗口不合法')
        if len(phase.deadlines_us) != 3 or min(phase.deadlines_us) <= 0:
            raise ValueError('三个固定对象均须有正期限')
        if i and phases[i - 1].end_us != phase.start_us:
            raise ValueError('需求窗口须连续')
    for effective, policy in policies:
        if not math.isfinite(effective) or policy.scope not in ('flow', 'port') or policy.selection not in ('prefix', 'reservoir'):
            raise ValueError('配置时间、索引或选择方式错误')
        fmt = FORMATS[policy.format_name]
        if policy.selection == 'reservoir' and not fmt.reservoir:
            raise ValueError('格式缺少蓄水池累计字段')
        if policy.selection == 'reservoir' and any(p.capacity != 1 for p in packets):
            raise ValueError('蓄水池参照目前只支持 K=1')
        if len(policy.ratios) != 3 or any(not isinstance(n, int) or n < 1 for n in policy.ratios):
            raise ValueError('源端采样比例错误')
        if len(policy.counter_offsets) != 3 or any(not isinstance(n, int) or n < 0 for n in policy.counter_offsets):
            raise ValueError('初始计数相位错误')
        if len(policy.weights) != 3 or any(not isinstance(n, int) or not 1 <= n <= 31 for n in policy.weights):
            raise ValueError('整数权重错误')
        for (node, flow), threshold in policy.thresholds.items():
            if node not in (1, 2, 3) or flow not in ((0,) if policy.scope == 'port' else (1, 2, 3)):
                raise ValueError('门控索引错误')
            if threshold is not None and (not math.isfinite(threshold) or threshold <= 0):
                raise ValueError('阈值须正，禁用用 None 表示')
    if any(b[0] <= a[0] for a, b in zip(policies, policies[1:])) or policies[0][0] > packets[0].gate_us[0]:
        raise ValueError('配置须提前生效且时间严格递增')
    if len({p.format_name for _, p in policies}) != 1 or len({p.counter_offsets for _, p in policies}) != 1:
        raise ValueError('本参照不支持运行中改格式或重置计数相位')
    for effective, policy in policies:
        for p in packets:
            if any((node, 0 if policy.scope == 'port' else p.flow) not in policy.thresholds for node in p.path):
                raise ValueError('配置未覆盖所有物理机会')
            if policies[0][0] != effective and p.gate_us[0] < effective <= p.generated_us[-1]:
                raise ValueError('不支持配置切换期间的在途包')


def replay(packets, policies, seed=0, detailed=False):
    """生效时刻是输入，不估计或免费生成控制观察。调用者需单独核对下发与费用。"""
    rng = random.Random(seed)
    gates, counts = {}, list(policies[0][1].counter_offsets)
    samples = {node: [] for node in (1, 2, 3)}
    wire, exports, inserted, selected = [0, 0, 0], 0, [0, 0, 0], {}
    events = []
    phase = 0
    for p in packets:
        # 目前只允许包之间完成整份配置，排除下发期间在途包。
        while phase + 1 < len(policies) and policies[phase + 1][0] <= p.gate_us[0]:
            phase += 1
        policy = policies[phase][1]
        fmt = FORMATS[policy.format_name]
        counts[p.flow - 1] += 1
        active = counts[p.flow - 1] % policy.ratios[p.flow - 1] == 0 and p.ip_bytes + fmt.header_bytes <= 1500
        records, seen, length = [], 0, p.ip_bytes + active * fmt.header_bytes
        for index, (node, edge) in enumerate(zip(p.path, p.links)):
            key = node, 0 if policy.scope == 'port' else p.flow
            threshold = policy.thresholds[key]
            now = p.gate_us[index]
            decision = 'no_envelope'
            draw = None
            if active:
                if threshold is None:
                    decision = 'disabled'
                elif key in gates and now - gates[key] < threshold:
                    decision = 'time'
                elif len(records) >= p.capacity and policy.selection == 'prefix':
                    decision = 'capacity'
                elif (not records or policy.selection == 'prefix') and length + fmt.record_bytes > 1500:
                    decision = 'mtu'
                else:
                    choose = True
                    if policy.selection == 'reservoir':
                        weight = policy.weights[node - 1]
                        seen += weight
                        draw = rng.randint(1, seen)
                        choose = not records or draw <= weight
                    if choose:
                        replaced = policy.selection == 'reservoir' and bool(records)
                        decision = 'replace' if replaced else 'insert'
                        if not replaced:
                            length += fmt.record_bytes
                        records = [(node, p.generated_us[index])] if replaced else [(node, p.generated_us[index])] + records
                        gates[key] = now
                        inserted[node - 1] += 1
                    else:
                        decision = 'random_reject'
            # 用每跳当时实际记录计费，保留已被下游替换的上游成本。
            wire[edge] += length - p.ip_bytes
            if detailed:
                events.append({'packet_id': p.packet_id, 'node': node, 'link': edge,
                               'decision': decision, 'draw': draw, 'extra_bytes': length - p.ip_bytes,
                               'gate_us': now, 'policy_index': phase})
        selected[p.packet_id] = [n for n, _ in records]
        if records and p.export_emitted:
            exports += fmt.export_bytes(len(records))
        if p.arrival_us is not None:
            for node, generated in records:
                samples[node].append((generated, p.arrival_us))
    result = {'samples': samples, 'selected': selected, 'inband_per_link_bytes': wire,
              'export_bytes': exports, 'data_total_byte_hops': sum(wire) + exports,
              'successful_insertions': inserted}
    if detailed:
        result['events'] = events
    return result


def violations(samples, phases):
    """穷举时仅积分违约时间，完整年龄分位值留给最终配置。"""
    rows = []
    for phase in phases:
        values = []
        for node, deadline in enumerate(phase.deadlines_us, 1):
            latest, cursor, bad = None, phase.start_us, 0.0
            for generated, arrived in sorted(samples[node], key=lambda x: (x[1], x[0])):
                if arrived <= phase.start_us:
                    latest = generated if latest is None else max(latest, generated)
                    continue
                if arrived >= phase.end_us:
                    break
                bad += arrived - cursor if latest is None else min(arrived - cursor, max(0, arrived - max(cursor, latest + deadline)))
                cursor = arrived
                latest = generated if latest is None else max(latest, generated)
            bad += phase.end_us - cursor if latest is None else min(phase.end_us - cursor, max(0, phase.end_us - max(cursor, latest + deadline)))
            values.append(bad / (phase.end_us - phase.start_us))
        rows.append(values)
    return rows


def evaluate(result, phases):
    return [{'window_us': [p.start_us, p.end_us], 'tasks':
             [age_metrics(Task(n, d), result['samples'][n], p.start_us, p.end_us)
              for n, d in enumerate(p.deadlines_us, 1)]} for p in phases]


def fork_packets(period_us=100000, start_us=-800000, end_us=2400000, delay_us=100):
    packets, pid = [], 0
    for base in range(start_us, end_us, period_us):
        entries = [(base, 1, (1, 3), (0, 2)), (base + period_us // 2, 2, (2, 3), (1, 2))]
        if (base - start_us) // period_us % 2 == 0:
            entries.append((base + period_us // 4, 3, (3,), (2,)))
        for t, flow, path, links in entries:
            if t >= end_us:
                continue
            pid += 1
            times = tuple(t + i * 20 for i in range(len(path)))
            packets.append(Opportunity(pid, flow, path, links, times, tuple(t + 5 for t in times), times[-1] + delay_us))
    # 重新编号便于实际发包。第三条流的资格只有合流后的共同出口。
    packets.sort(key=lambda p: p.gate_us[0])
    return [Opportunity(i + 1, p.flow, p.path, p.links, p.gate_us, p.generated_us, p.arrival_us)
            for i, p in enumerate(packets)]


def static_policies(grid, scope, reservoir=False):
    keys = [(1, 1), (2, 2), (3, 1), (3, 2), (3, 3)] if scope == 'flow' else [(1, 0), (2, 0), (3, 0)]
    # 分支仅一条流；五个实际 TT-INT 门控索引分别调参。
    source_options = [(n, offset) for n in (1, 2) for offset in range(n)]
    weights = sorted({tuple(w // math.gcd(*row) for w in row) for row in product((1, 2, 4), repeat=3)}) if reservoir else [(1, 1, 1)]
    for thresholds in product(grid, repeat=len(keys)):
        for options in product(source_options, repeat=3) if not reservoir else [((1, 0),) * 3]:
            for weight in weights:
                yield Policy(dict(zip(keys, thresholds)), scope, tuple(o[0] for o in options),
                             tuple(o[1] for o in options), 'reservoir' if reservoir else 'prefix',
                             weight, 'task' if reservoir else 'prefix')
