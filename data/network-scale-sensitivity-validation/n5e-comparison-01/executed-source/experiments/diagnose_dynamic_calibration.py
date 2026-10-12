"""核对全局参数选择后的同业务配对、预算资格与年龄条件差值。"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import shutil

from diagnose_static_transfer import signature


METHODS = ('static', 'feedback', 'local', 'joint', 'phase_joint',
           'uniform_guard', 'phase_guard', 'phase_local_guard')
PROFILES = (('stable', 'high'), ('shared_priority', 'high'),
            ('switch', 'low'), ('bursty_jitter', 'low'))
LAYERS = ('e1', 'e2', 'e3', 'export', 'api', 'local_notice')


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def difference(a, b):
    lo, hi = a['loss_lower'] - b['loss_upper'], a['loss_upper'] - b['loss_lower']
    if not all(math.isfinite(x) for x in (lo, hi)) or lo > hi:
        raise ValueError('年龄条件差值不可评价')
    return {'lower': lo, 'upper': hi,
            'direction': 'lower' if hi < 0 else 'higher' if lo > 0 else 'overlap'}


def rates(row):
    return {layer: [row[layer + '_' + side + '_bytes_per_s'] for side in ('lower', 'upper')]
            if layer in LAYERS[:4] else [row[layer + '_bytes_per_s']] * 2 for layer in LAYERS}


def diagnose(analysis, output):
    manifest = load(analysis / 'manifest.json')
    if manifest['status'] != 'passed' or len(manifest['cases']) != 184 or manifest['smoke']:
        raise ValueError('必须使用完整的有限网络校准')
    inputs = ['selected-calibration-cases.json', 'frozen-parameters.json', 'summary-checks.json']
    for case in manifest['cases']:
        inputs += [case['name'] + '/' + name for name in
                   ('packets.json', 'collector.jsonl', 'tasks.json', 'generation-bounds.json',
                    'controller-visible-bounds.json', 'window-costs.json')]
    for name in inputs:
        if sha(analysis / name) != manifest['artifact_sha256'][name]:
            raise ValueError('配对输入摘要不同: ' + name)
    if load(analysis / 'summary-checks.json')['status'] != 'passed':
        raise ValueError('校准分析未独立通过')
    selected = load(analysis / 'selected-calibration-cases.json')
    keys = {(r['scenario'], r['budget_level'], r['seed'], r['method']): r for r in selected}
    expected = {(s, b, seed, method) for s, b in PROFILES
                for seed in (1301, 1423) for method in METHODS}
    if len(keys) != len(selected) or set(keys) != expected:
        raise ValueError('完整选中方法/业务展示集合不符')
    identities = {c['name']: c for c in manifest['cases']}
    frozen = {r['method']: r['selected_parameter'] for r in load(analysis / 'frozen-parameters.json')}
    signatures, controllers = {}, {}
    for r in selected:
        identity = identities[r['case']]
        if any(r[k] != identity[k] for k in ('scenario', 'budget_level', 'seed', 'method', 'parameter_index')):
            raise ValueError('配对身份不同')
        if r['method'] in frozen and r['parameter_index'] != frozen[r['method']]:
            raise ValueError('配对未使用冻结全局参数')
        directory = analysis / r['case']
        age, cost = load(directory / 'generation-bounds.json'), load(directory / 'window-costs.json')
        if any(r[k] != age[k] for k in ('loss_lower', 'loss_upper')):
            raise ValueError('展示年龄与实测来源不同')
        if r['window_feasibility'] != cost['window_feasibility']:
            raise ValueError('展示预算资格与来源不同')
        for layer, bounds in rates(r).items():
            actual = ([cost['measurement_' + side + '_bytes'][layer] / 8 for side in ('lower', 'upper')]
                      if layer in LAYERS[:4] else [cost[layer + '_bytes_per_s']] * 2)
            if bounds != actual:
                raise ValueError('展示逐层费用与实际来源不同')
        signatures[r['case']] = signature(directory)
        controllers[r['case']] = load(directory / 'controller-visible-bounds.json')
    pairs = []
    for s, b in PROFILES:
        for seed in (1301, 1423):
            static = keys[s, b, seed, 'static']
            sw, ss = signatures[static['case']]
            for method in METHODS[1:]:
                dynamic = keys[s, b, seed, method]
                dw, ds = signatures[dynamic['case']]
                if sw != dw or load(analysis / static['case'] / 'tasks.json') != load(analysis / dynamic['case'] / 'tasks.json'):
                    raise ValueError('配对实际业务或完整任务不同')
                pairs.append({'scenario': s, 'budget_level': b, 'seed': seed, 'method': method,
                    'parameter_index': dynamic['parameter_index'], 'static_case': static['case'],
                    'dynamic_case': dynamic['case'],
                    'both_within': static['window_feasibility'] == dynamic['window_feasibility'] == 'within',
                    'static_feasibility': static['window_feasibility'],
                    'dynamic_feasibility': dynamic['window_feasibility'],
                    'collector_difference': difference(dynamic, static),
                    'controller_difference': difference(controllers[dynamic['case']], controllers[static['case']]),
                    'static_loss_bounds': [static['loss_lower'], static['loss_upper']],
                    'dynamic_loss_bounds': [dynamic['loss_lower'], dynamic['loss_upper']],
                    'static_fee_rates': rates(static), 'dynamic_fee_rates': rates(dynamic),
                    'same_sampling_positions': ss == ds,
                    'position_symmetric_difference': len(set(ss) ^ set(ds)),
                    'static_sample_count': len(ss), 'dynamic_sample_count': len(ds)})
    groups = []
    for s, b in PROFILES:
        for method in METHODS[1:]:
            rows = [r for r in pairs if (r['scenario'], r['budget_level'], r['method']) == (s, b, method)]
            groups.append({'scenario': s, 'budget_level': b, 'method': method, 'pairs': len(rows),
                'both_within_pairs': sum(r['both_within'] for r in rows),
                'collector_directions': dict(Counter(r['collector_difference']['direction'] for r in rows)),
                'controller_directions': dict(Counter(r['controller_difference']['direction'] for r in rows)),
                'both_eligible_collector_lower': all(r['both_within'] and r['collector_difference']['upper'] < 0 for r in rows),
                'mean_static_loss_bounds': [sum(r['static_loss_bounds'][i] for r in rows) / 2 for i in (0, 1)],
                'mean_dynamic_loss_bounds': [sum(r['dynamic_loss_bounds'][i] for r in rows) / 2 for i in (0, 1)],
                'mean_static_fee_rates': {layer: [sum(r['static_fee_rates'][layer][i] for r in rows) / 2 for i in (0, 1)] for layer in LAYERS},
                'mean_dynamic_fee_rates': {layer: [sum(r['dynamic_fee_rates'][layer][i] for r in rows) / 2 for i in (0, 1)] for layer in LAYERS}})
    findings = {'status': 'passed', 'selected_cases': len(selected), 'pairs': len(pairs), 'groups': len(groups),
        'both_within_pairs': sum(r['both_within'] for r in pairs),
        'same_sampling_position_pairs': sum(r['same_sampling_positions'] for r in pairs),
        'eligible_lower_groups': [{k: r[k] for k in ('scenario', 'budget_level', 'method')} for r in groups if r['both_eligible_collector_lower']],
        'scope': '全局参数选择后的事后校准配对，条件差值不是置信区间，不改变已冻结参数；两种业务不是部署重复，双方达标单独判断'}
    output.mkdir(parents=True, exist_ok=False)
    def write(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    write('paired-cases.json', pairs)
    write('paired-groups.json', groups)
    write('findings.json', findings)
    sources = (Path(__file__).resolve(), Path(__file__).resolve().with_name('diagnose_static_transfer.py'))
    for source in sources:
        target = output / 'executed-source/experiments' / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    write('manifest.json', {'status': 'passed', 'analysis_manifest_sha256': sha(analysis / 'manifest.json'),
        'input_sha256': {n: sha(analysis / n) for n in inputs},
        'source_sha256': {'experiments/' + p.name: sha(p) for p in sources}, 'findings': findings,
        'artifact_sha256': {str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}})
    print(json.dumps(findings, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    diagnose(args.analysis, args.output)
