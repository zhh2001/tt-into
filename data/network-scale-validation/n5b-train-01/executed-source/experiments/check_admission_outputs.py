"""核对保存摘要，并独立重放最终 56 个 TT-INT 候选的资格、成本和连续年龄。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from orchestration.path_reference import Opportunity, Phase, Policy, evaluate, replay, validate
from orchestration.trace_admission import selection_cost
from orchestration.budget_model import FORMATS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--tuned', type=Path, required=True)
    parser.add_argument('--runs', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    checks = []
    for directory in args.runs:
        manifest = json.loads((directory / 'manifest.json').read_text())
        if manifest['status'] != 'passed':
            raise ValueError('运行未通过')
        for name, expected in manifest['artifact_sha256'].items():
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
                raise ValueError(f'产物摘要错误 {directory.name}/{name}')
        for name, expected in manifest['source_sha256'].items():
            if hashlib.sha256((directory / 'executed-source' / name).read_bytes()).hexdigest() != expected:
                raise ValueError(f'实际运行源码摘要错误 {directory.name}/{name}')
        checks.append({'run_id': directory.name, 'manifest_sha256': hashlib.sha256((directory / 'manifest.json').read_bytes()).hexdigest(),
                       'artifacts_verified': len(manifest['artifact_sha256']), 'executed_sources_verified': len(manifest['source_sha256'])})
    inputs = json.loads((args.base / 'inputs.json').read_text())
    phases = [Phase(**{**p, 'deadlines_us': tuple(p['deadlines_us'])}) for p in inputs['phases']]
    cases = []
    for row in json.loads((args.tuned / 'results.json').read_text()):
        packets = [Opportunity(**{**p, **{k: tuple(p[k]) for k in ('path', 'links', 'gate_us', 'generated_us')},
                                  'capacity': row['capacity']}) for p in inputs['packets']]
        best = row['native_best']
        early, late = [Policy({(v['node'], v['flow']): v['threshold_us'] for v in best[k]}) for k in ('early', 'late')]
        cold = dict.fromkeys(early.thresholds, None)
        shared = {**cold, (3, 3): early.thresholds[3, 3]}
        first = {**shared, (1, 1): early.thresholds[1, 1], (3, 1): early.thresholds[3, 1]}
        second = {**first, (2, 2): early.thresholds[2, 2], (3, 2): early.thresholds[3, 2]}
        schedule = [(-800000, Policy(cold)), (-175001, Policy(shared)), (-100001, Policy(first)),
                    (-50001, Policy(second)), (0, early), (1200000, late)]
        validate(packets, phases, schedule)
        actual = replay(packets, schedule)
        cost = selection_cost(packets, actual['selected'], {p.packet_id: True for p in packets}, FORMATS['prefix'])
        for key in cost:
            if cost[key] != best[key]:
                raise ValueError(f'原始事件重放费用不符 {key}')
        # 调参用快速违约积分，核查改用独立完整年龄积分。
        metrics = evaluate(actual, phases)
        accepted = [t['node'] for t in inputs['tasks'] if all(
            next(r for r in phase['tasks'] if r['node'] == t['node'])['violation_fraction'] <= 1e-10 for phase in metrics)]
        if accepted != best['accepted_nodes'] or sum(t['weight'] for t in inputs['tasks'] if t['node'] in accepted) != row['upper_weight']:
            raise ValueError('独立年龄核查未达到原始上界')
        if any(v > b for v, b in zip(cost['inband_per_link_bytes'], row['budget']['inband_per_link_bytes'])) or cost['export_bytes'] > row['budget']['export_bytes']:
            raise ValueError('独立费用超限')
        cases.append({'capacity': row['capacity'], 'budget': row['budget'], 'accepted_nodes': accepted,
                      'weight': row['upper_weight'], 'cost': cost})
    args.output.mkdir(parents=True, exist_ok=False)
    sources = ['experiments/check_admission_outputs.py', 'src/orchestration/path_reference.py',
               'src/orchestration/trace_admission.py', 'src/orchestration/model.py',
               'src/orchestration/metrics.py', 'src/orchestration/budget_model.py']
    for name in sources:
        target = args.output / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    result = {'status': 'passed', 'verified_runs': checks, 'verified_final_candidates': len(cases), 'cases': cases,
              'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
              'scope': '源文件与产物完整性、事件资格、独立费用与连续年龄核查，不是网络测量'}
    (args.output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print('verified runs', len(checks), 'final candidates', len(cases))


if __name__ == '__main__':
    main()
