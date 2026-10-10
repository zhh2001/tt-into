"""把给定的逐流 TT-INT 参数转成保留成功状态的寄存器更新。"""
from dataclasses import dataclass

KEYS = ((1, 1), (2, 2), (3, 1), (3, 2), (3, 3))


def parameters(rows):
    values = {(r['node'], r['flow']): r['threshold_us'] for r in rows}
    if len(rows) != len(KEYS) or set(values) != set(KEYS):
        raise ValueError('固定分支须明确声明全部五个逐流索引')
    if any(v is not None and (not isinstance(v, int) or isinstance(v, bool) or not 1 <= v < 1 << 48)
           for v in values.values()):
        raise ValueError('阈值须为正的 48 bit 微秒数或关闭状态')
    return values


@dataclass(frozen=True)
class Write:
    node: int
    flow: int
    register: str
    value: int


def transition(old, new, capacity):
    if set(old) != set(KEYS) or set(new) != set(KEYS) or capacity not in (1, 2):
        raise ValueError('不支持的参数索引或容量')
    writes = []
    for node, flow in KEYS:
        before, after = old[node, flow], new[node, flow]
        if before == after:
            continue
        if after is None:
            writes.append(Write(node, flow, 'TTEgress.max_count', 0))
        else:
            # 在启用前写好阈值，不重置 last_timestamp 或 has_sample。
            writes.append(Write(node, flow, 'TTEgress.time_threshold', after))
            if before is None:
                writes.append(Write(node, flow, 'TTEgress.max_count', capacity))
    return writes


def activation_plan(early_rows, late_rows, capacity):
    early, late = parameters(early_rows), parameters(late_rows)
    # 冷启动预装 early 阈值，门控容量全为零。后续只启用容量。
    current = dict.fromkeys(KEYS, None)
    stages = []
    for offset, keys in [(615000, ((3, 3),)), (690000, ((1, 1), (3, 1))),
                         (740000, ((2, 2), (3, 2)))]:
        new = current.copy()
        writes = []
        for key in keys:
            new[key] = early[key]
            if early[key] is not None:
                writes.append(Write(*key, 'TTEgress.max_count', capacity))
        stages.append({'offset_us': offset, 'kind': 'initial_activation',
                       'parameters': new, 'writes': writes})
        current = new
    stages.append({'offset_us': 1980000, 'kind': 'online_update', 'parameters': late,
                   'writes': transition(current, late, capacity)})
    return stages
