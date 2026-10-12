"""不调用选择器，按完整分母和有理数均值重建全局排序。"""
import argparse
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path

from check_rolling_reference import same, near
from literature_stage_contract import specification, sha


def average(values):
    return float(sum((Fraction(float(v)) for v in values), Fraction()) / len(values))


def check(directory):
    load = lambda name: json.loads((directory / name).read_text())
    manifest, selected, rows = load('manifest.json'), load('selection.json'), load('score-inputs.json')
    for name, digest in manifest['artifact_sha256'].items(): same(sha(directory / name), digest, '选择输入摘要')
    spec, stage = specification(), selected['stage']
    if stage not in ('training', 'calibration'): raise ValueError('独立测试不能用于选择')
    candidates = manifest['training_selected_indices']
    indices = {m: range(6) for m in spec['literature_methods']} if stage == 'training' else candidates
    profiles = [(p['scenario'], p['budget_level']) for p in spec['profiles']]
    actual = Counter((r['scenario'], r['budget_level'], int(r['seed']), r['method'], int(r['preset_index'])) for r in rows)
    expected = Counter((s, b, seed, m, i) for s, b in profiles for seed in spec[stage]['seeds'] for m, values in indices.items() for i in values)
    same(actual, expected, '全部预设、任务和业务分母')
    ranks, count = {}, 0
    for score in selected['scores']:
        m, index = score['method'], score['preset_index']
        group = [r for r in rows if r['method'] == m and int(r['preset_index']) == index]
        same(score['runs'], 8, '每全局预设八例')
        same(score['parameters'], spec['parameter_grids'][m][index], '预声明参数绑定')
        failures = sum(r['window_feasibility'] != 'within' for r in group)
        worst = max(average([r['controller_loss_upper'] for r in group if (r['scenario'], r['budget_level']) == p]) for p in profiles)
        key = [failures, worst, average([r['controller_loss_upper'] for r in group]), average([r['api_bytes_per_s'] for r in group]), index]
        for a, b in zip(score['score'], key): near(a, b, '独立有理数均值评分')
        ranks[m, index] = key; count += 7
    same(set(ranks), {(m, i) for m, values in indices.items() for i in values}, '完整评分分母')
    n = 2 if stage == 'training' else 1
    chosen = {m: sorted(values, key=lambda i: ranks[m, i])[:n] for m, values in indices.items()}
    same(selected['selected_indices'], chosen, '独立全局排序和冻结')
    same(selected['row_count'], len(rows), '全部结果保留')
    return dict(status='passed', stage=stage, selection_checks=count + len(rows) + 4,
                selection_manifest_sha256=sha(directory / 'manifest.json'), selected_indices=chosen)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--selection', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); result = check(a.selection)
    with a.output.open('x') as stream: json.dump(result, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps(result, ensure_ascii=False))
