"""有界候选及逐周期分配修复，保留文献适配的收益目标。"""
from itertools import chain, product
from orchestration.literature import assignment, unique_assignment
from orchestration.rolling import changes, neighbors, shifted


def duplicates(config, keys):
    nodes = [n for value, (n, _) in zip(config, keys) if value is not None]
    return len(nodes) - len(set(nodes))


def unique_starts(proxy):
    keys = proxy.management.keys
    groups = [[None] + [i for i, (n, _) in enumerate(keys) if n == node] for node in sorted({n for n, _ in keys})]
    for chosen in product(*groups):
        selected = set(chosen) - {None}
        yield tuple(proxy.settings.grid_us[0] if i in selected else None for i in range(len(keys)))


def select_bounded(proxy, trained):
    if not proxy.ready or not proxy.demands:
        return dict(config=proxy.current, reason='insufficient_observations', evaluated_candidates=0, forecast=None)
    current = proxy.evaluate(proxy.current)
    selected = current
    duplicate_count = duplicates(proxy.current, proxy.management.keys)
    # 共同初始化可能需要多于6次许可写，先给出一次可执行的去重修复。
    repair, seen, writes = list(proxy.current), set(), 0
    for i, (value, (node, _)) in enumerate(zip(proxy.current, proxy.management.keys)):
        if value is None: continue
        if node in seen and writes < proxy.settings.max_update_rpcs:
            repair[i] = None; writes += 1
        else: seen.add(node)
    pool = chain((tuple(repair), tuple(trained), shifted(proxy.current,-1,proxy.settings), shifted(proxy.current,1,proxy.settings)),
                 neighbors(proxy.current, proxy.settings), unique_starts(proxy))
    attempted = 0
    for config in pool:
        # 搜索生成量也有界，防止容量不允许的起点形成指数遍历。
        attempted += 1
        if attempted > proxy.settings.max_candidates * 4: break
        if config not in proxy.memo and len(proxy.memo) >= proxy.settings.max_candidates: break
        if len(changes(config, proxy.fields, proxy.management)) > proxy.settings.max_update_rpcs: continue
        count = duplicates(config, proxy.management.keys)
        if count > duplicate_count or count and duplicate_count == 0: continue
        row = proxy.evaluate(config)
        # 达到唯一分配前逐周期减少重复，之后沿用原收益/稳定性目标。
        rank = (round(row['predicted_excess'],12), count, *proxy.rank(row)[1:])
        previous = (round(selected['predicted_excess'],12), duplicates(selected['config'],proxy.management.keys), *proxy.rank(selected)[1:])
        if row['update_allowed'] and rank < previous: selected = row
    return dict(config=selected['config'], reason=proxy.method, evaluated_candidates=len(proxy.memo), forecast=selected,
                generated_candidates=attempted, assignment_repair=duplicate_count > 0)
