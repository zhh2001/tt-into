"""完整训练/校准矩阵的全局参数选择，保留全部预算失败。"""
from collections import Counter
import math
import statistics


def select(rows, stage, protocol, candidates=None):
    if stage not in ('training', 'calibration'):
        raise ValueError('参数选择只接受训练或校准')
    seeds = protocol[stage]['seeds']
    profiles = [(p['scenario'], p['budget_level']) for p in protocol['profiles']]
    grids = protocol['parameter_grids']
    indices = {m: list(range(len(g))) for m, g in grids.items()} if stage == 'training' else candidates
    if indices is None or set(indices) != set(grids):
        raise ValueError('校准候选方法不完整')
    expected = Counter((s, b, seed, method, index) for s, b in profiles for seed in seeds
                       for method, presets in indices.items() for index in presets)
    actual = Counter((r['scenario'], r['budget_level'], int(r['seed']), r['method'], int(r['preset_index'])) for r in rows)
    if actual != expected:
        raise ValueError('训练/校准分母缺失、重复或包含其他业务')
    scores = []
    for method, presets in indices.items():
        for index in presets:
            group = [r for r in rows if r['method'] == method and int(r['preset_index']) == index]
            losses = [float(r['controller_loss_upper']) for r in group]
            apis = [float(r['api_bytes_per_s']) for r in group]
            if not all(math.isfinite(x) and x >= 0 for x in losses + apis):
                raise ValueError('选择评分必须有限且非负')
            worst = max(statistics.mean(float(r['controller_loss_upper']) for r in group
                        if (r['scenario'], r['budget_level']) == profile) for profile in profiles)
            violations = sum(r['window_feasibility'] != 'within' for r in group)
            key = [violations, worst, statistics.mean(losses), statistics.mean(apis), index]
            scores.append(dict(method=method, preset_index=index, parameters=grids[method][index],
                               runs=len(group), score=key))
    count = 2 if stage == 'training' else 1
    chosen = {method: [r['preset_index'] for r in sorted((r for r in scores if r['method'] == method), key=lambda r: r['score'])[:count]]
              for method in grids}
    return dict(status='training_candidates_frozen' if stage == 'training' else 'literature_parameters_frozen',
                stage=stage, scores=scores, selected_indices=chosen, global_parameters=True,
                selection_key=protocol['selection_key'], row_count=len(rows))
