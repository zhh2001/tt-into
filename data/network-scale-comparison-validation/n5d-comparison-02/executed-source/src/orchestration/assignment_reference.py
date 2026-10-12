"""固定路径的 D-INTO P3 与 INTOPP 单时隙分配参照，不含学习或网络执行。"""
from dataclasses import dataclass
from itertools import product
import math

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


@dataclass(frozen=True)
class Item:
    node: int
    kind: int
    size_bytes: int
    gain: float = 1.0

    @property
    def key(self):
        return self.node, self.kind


@dataclass(frozen=True)
class Flow:
    flow: int
    path: tuple[int, ...]
    capacity_bytes: int


@dataclass(frozen=True)
class Dependency:
    key: str
    nodes: tuple[int, ...]
    kinds: tuple[int, ...]
    spatial: bool
    active: bool = True
    weight: float = 1.0
    # 原 INTOPP 式 (5) 只统计各自 Hf>Tf 的流。None 为全部流，空元组为没有到期流。
    flows: tuple[int, ...] | None = None


@dataclass(frozen=True)
class IntoppGroup:
    key: str
    kinds: tuple[int, ...]
    temporal: bool = False


def _finite(value, lower=0):
    return isinstance(value, (int, float)) and math.isfinite(value) and value >= lower


def _prepare(items, flows, previous):
    items, flows = tuple(sorted(items, key=lambda x: x.key)), tuple(sorted(flows, key=lambda x: x.flow))
    if not items or not flows or len({i.key for i in items}) != len(items) or len({f.flow for f in flows}) != len(flows):
        raise ValueError('任务、流不能为空或重复')
    for i in items:
        if any(type(v) is not int or v < 1 for v in (i.node, i.kind, i.size_bytes)) or not _finite(i.gain):
            raise ValueError('遥测项标识、大小或收益不合法')
    for f in flows:
        if type(f.flow) is not int or f.flow < 1 or not f.path or len(set(f.path)) != len(f.path):
            raise ValueError('流标识或固定路径不合法')
        if any(type(n) is not int or n < 1 for n in f.path) or type(f.capacity_bytes) is not int or f.capacity_bytes < 0:
            raise ValueError('流路径或字节容量不合法')
    pairs = [(i.key, f.flow) for i in items for f in flows if i.node in f.path]
    previous = dict(previous or {})
    if any((key, flow) not in pairs for key, flow in previous.items()):
        raise ValueError('先前指派含未知项或不合格路径')
    # 上一时隙可以使用更大的容量，不能按当前容量拒绝其历史状态。
    return items, flows, pairs, previous


def feasible_assignments(items, flows, maximum=1000000):
    """小规模准确枚举。0 仅在内部表示未分配，返回值不包含未分配项。"""
    items, flows, _, _ = _prepare(items, flows, {})
    options = [[0] + [f.flow for f in flows if i.node in f.path] for i in items]
    if math.prod(map(len, options)) > maximum:
        raise ValueError('枚举空间超过声明上限，应使用 MILP')
    for row in product(*options):
        if all(sum(i.size_bytes for i, chosen in zip(items, row) if chosen == f.flow) <= f.capacity_bytes for f in flows):
            yield {i.key: chosen for i, chosen in zip(items, row) if chosen}


def variation_bytes(items, assignment, previous):
    """按项长度计量指派变化。换流包含撤销和新增两次，不是实际控制字节。"""
    return sum(i.size_bytes * ((i.key in assignment) + (i.key in previous))
               for i in items if assignment.get(i.key) != previous.get(i.key))


def _base(items, flows, pairs, previous, extra=0):
    n = len(pairs) + extra
    rows, upper = [], []
    for i in items:
        rows.append([float(k == i.key) for k, _ in pairs] + [0.] * extra)
        upper.append(1.)
    for f in flows:
        rows.append([float(next(i.size_bytes for i in items if i.key == k)) if flow == f.flow else 0.
                     for k, flow in pairs] + [0.] * extra)
        upper.append(float(f.capacity_bytes))
    sizes = {i.key: i.size_bytes for i in items}
    delta = [sizes[k] * (1 - 2 * (previous.get(k) == f)) for k, f in pairs] + [0.] * extra
    constant = sum(sizes[k] for k in previous)
    return n, rows, upper, delta, constant


def _flow_order(flows, order):
    order = tuple(f.flow for f in flows) if order is None else tuple(order)
    if len(set(order)) != len(order) or set(order) != {f.flow for f in flows}:
        raise ValueError('平局选流顺序必须恰好覆盖全部流')
    return {flow: rank for rank, flow in enumerate(order, 1)}


def _optimize(primary, rows, upper, delta, items, pairs, rank):
    """依次优化原目标、变更量、按项排序的流号。辅助布尔变量不影响指派顺序。"""
    n = len(primary)
    if not n:
        return np.zeros(0), 0
    matrix = [list(row) for row in rows]
    low, high = [-np.inf] * len(rows), list(upper)
    calls = 0

    def run(objective):
        nonlocal calls
        result = milp(np.asarray(objective, dtype=float), integrality=np.ones(n), bounds=Bounds(0, 1),
                      constraints=LinearConstraint(np.asarray(matrix), low, high),
                      options={'mip_rel_gap': 0.0, 'time_limit': 10.0})
        calls += 1
        if result.status != 0:
            raise RuntimeError(f'单时隙 MILP 未证明最优: {result.status}, {result.message}')
        x = np.rint(result.x)
        if np.any(abs(result.x - x) > 1e-6):
            raise RuntimeError('求解器返回非整数指派')
        return x

    def lock(objective, x):
        # 固定已得到的离散目标值。狭窄区间会触发旧版 HiGHS 的预处理误判。
        value = float(np.dot(objective, x))
        matrix.append(list(objective))
        low.append(value)
        high.append(value)

    x = run(primary)
    lock(primary, x)
    x = run(delta)
    lock(delta, x)
    for item in items:
        lex = [float(rank[f]) if k == item.key else 0. for k, f in pairs] + [0.] * (n - len(pairs))
        x = run(lex)
        lock(lex, x)
    return x, calls


def solve_dinto(items, flows, previous=None, queue=0., tradeoff=1., average_change_bytes=0., method='milp', flow_order=None):
    """原 P3: min Q*Delta - V*In。虚拟队列按原式递推，收益由调用者给定。"""
    items, flows, pairs, previous = _prepare(items, flows, previous)
    if not _finite(queue) or not _finite(tradeoff) or tradeoff <= 0 or not _finite(average_change_bytes):
        raise ValueError('虚拟队列、V 或平均变更额度不合法')
    gains = {i.key: i.gain for i in items}
    rank = _flow_order(flows, flow_order)

    def values(a):
        delta = variation_bytes(items, a, previous)
        gain = sum(gains[k] for k in a)
        return queue * delta - tradeoff * gain, delta, tuple(rank[a[i.key]] if i.key in a else 0 for i in items)

    if method == 'enumeration':
        assignment = min(feasible_assignments(items, flows), key=values)
        calls = 0
    elif method == 'milp':
        _, rows, upper, delta, _ = _base(items, flows, pairs, previous)
        primary = [queue * d - tradeoff * gains[k] for d, (k, _) in zip(delta, pairs)]
        x, calls = _optimize(primary, rows, upper, delta, items, pairs, rank)
        assignment = {k: f for (k, f), chosen in zip(pairs, x) if chosen}
    else:
        raise ValueError('未知准确求解方式')
    objective, change, _ = values(assignment)
    return {'assignment': assignment, 'information_gain': sum(gains[k] for k in assignment),
            'variation_bytes': change, 'p3_objective': objective,
            'next_queue': max(0., queue + change - average_change_bytes), 'solver_calls': calls}


def _dependencies(dependencies):
    dependencies = tuple(dependencies)
    if len({d.key for d in dependencies}) != len(dependencies):
        raise ValueError('依赖标识重复')
    for d in dependencies:
        if not d.key or not d.nodes or not d.kinds or len(set(d.nodes)) != len(d.nodes) or len(set(d.kinds)) != len(d.kinds):
            raise ValueError('依赖标识、节点或类型集合不合法')
        if any(type(n) is not int or n < 1 for n in d.nodes + d.kinds) or (d.spatial and len(d.nodes) != 1):
            raise ValueError('空间依赖必须来自同一节点')
        if type(d.spatial) is not bool or type(d.active) is not bool or not _finite(d.weight) or d.weight <= 0:
            raise ValueError('依赖权重或状态不合法')
        if d.flows is not None and (d.spatial or len(set(d.flows)) != len(d.flows) or any(type(f) is not int or f < 1 for f in d.flows)):
            raise ValueError('到期流筛选只能用于时间依赖，流标识须唯一且为正')
    return dependencies


def fulfilled_dependencies(assignment, dependencies, temporal='count'):
    if temporal not in ('count', 'distinct'):
        raise ValueError('时间依赖语义须为 count 或 distinct')
    fulfilled = []
    for d in _dependencies(dependencies):
        selected = {(node, kind) for (node, kind), flow in assignment.items()
                    if node in d.nodes and kind in d.kinds and (d.flows is None or flow in d.flows)}
        count = len(selected) if d.spatial or temporal == 'count' else len({kind for _, kind in selected})
        if d.active and count >= len(d.kinds):
            fulfilled.append(d.key)
    return fulfilled


def expired(elapsed, deadline, lookahead=0.):
    """H 解释为已流逝时间。原严格 H>T，强适配提前 lookahead，未知状态直接视为到期。"""
    if not _finite(deadline) or deadline <= 0 or not _finite(lookahead) or (elapsed is not None and not _finite(elapsed)):
        raise ValueError('经过时间、期限或提前量不合法')
    return elapsed is None or elapsed + lookahead > deadline


def native_intopp_dependencies(groups, nodes, flow_ids, elapsed_us, deadlines_us, lookahead_us=0.):
    """生成原式 (1)、(4)、(5) 的依赖与节点重复权重，不运行聚类。

    一个 group 是 R_s 中的一个类型集合，temporal=True 同时使其属于 R_t。
    时间状态与期限都按 (group.key, flow) 输入，未知状态必须显式写 None。
    """
    groups, nodes, flow_ids = tuple(groups), tuple(nodes), tuple(flow_ids)
    if not groups or not nodes or not flow_ids or len({g.key for g in groups}) != len(groups):
        raise ValueError('原模型的组、节点或流不能为空或重复')
    if len(set(nodes)) != len(nodes) or len(set(flow_ids)) != len(flow_ids) or any(type(x) is not int or x < 1 for x in nodes + flow_ids):
        raise ValueError('原模型节点与流标识不合法')
    dependencies = []
    for group in groups:
        if not isinstance(group.key, str) or not group.key or type(group.temporal) is not bool:
            raise ValueError('原模型依赖组标识或时间成员标记不合法')
        for node in nodes:
            dependencies.append(Dependency(f'{group.key}/spatial/{node}', (node,), group.kinds, True))
        if group.temporal:
            due = []
            for flow in flow_ids:
                key = group.key, flow
                if key not in elapsed_us or key not in deadlines_us:
                    raise ValueError('每个时间依赖需要逐流状态与期限，未知状态用 None')
                if expired(elapsed_us[key], deadlines_us[key], lookahead_us):
                    due.append(flow)
            dependencies.append(Dependency(f'{group.key}/temporal', nodes, group.kinds, False,
                                           weight=len(nodes), flows=tuple(due)))
    return _dependencies(dependencies)


def solve_intopp(items, flows, dependencies, previous=None, temporal='count', method='milp', flow_order=None):
    """原依赖计数分配与 distinct 强适配。原式外层节点重复权重由 Dependency.weight 明确输入。"""
    items, flows, pairs, previous = _prepare(items, flows, previous)
    dependencies = _dependencies(dependencies)
    if any(d.flows is not None and not set(d.flows) <= {f.flow for f in flows} for d in dependencies):
        raise ValueError('时间依赖到期筛选包含未知流')
    if temporal not in ('count', 'distinct'):
        raise ValueError('时间依赖语义须为 count 或 distinct')
    weights = {d.key: d.weight for d in dependencies}
    rank = _flow_order(flows, flow_order)

    def values(a):
        score = sum(weights[k] for k in fulfilled_dependencies(a, dependencies, temporal))
        return -score, variation_bytes(items, a, previous), tuple(rank[a[i.key]] if i.key in a else 0 for i in items)

    if method == 'enumeration':
        assignment = min(feasible_assignments(items, flows), key=values)
        calls = 0
    elif method == 'milp':
        distinct = [(j, kind) for j, d in enumerate(dependencies) if temporal == 'distinct' and not d.spatial for kind in d.kinds]
        extra = len(dependencies) + len(distinct)
        n, rows, upper, delta, _ = _base(items, flows, pairs, previous, extra)
        z_index = {key: len(pairs) + len(dependencies) + idx for idx, key in enumerate(distinct)}
        for j, d in enumerate(dependencies):
            row = [0.] * n
            y = len(pairs) + j
            row[y] = float(len(d.kinds))
            if d.spatial or temporal == 'count':
                for idx, ((node, kind), flow) in enumerate(pairs):
                    if node in d.nodes and kind in d.kinds and (d.flows is None or flow in d.flows):
                        row[idx] = -1.
            else:
                for kind in d.kinds:
                    z = z_index[j, kind]
                    row[z] = -1.
                    cover = [0.] * n
                    cover[z] = 1.
                    for idx, ((node, typ), flow) in enumerate(pairs):
                        if node in d.nodes and typ == kind and (d.flows is None or flow in d.flows):
                            cover[idx] = -1.
                    rows.append(cover)
                    upper.append(0.)
            rows.append(row)
            upper.append(0.)
            if not d.active:
                off = [0.] * n
                off[y] = 1.
                rows.append(off)
                upper.append(0.)
        primary = [0.] * len(pairs) + [-d.weight for d in dependencies] + [0.] * len(distinct)
        x, calls = _optimize(primary, rows, upper, delta, items, pairs, rank)
        assignment = {k: f for (k, f), chosen in zip(pairs, x) if chosen}
    else:
        raise ValueError('未知准确求解方式')
    fulfilled = fulfilled_dependencies(assignment, dependencies, temporal)
    return {'assignment': assignment, 'fulfilled_dependencies': fulfilled,
            'dependency_score': sum(weights[k] for k in fulfilled),
            'variation_bytes': variation_bytes(items, assignment, previous), 'solver_calls': calls}
