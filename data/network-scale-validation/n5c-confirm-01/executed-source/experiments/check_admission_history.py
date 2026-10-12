"""固定各资源点最佳 TT-INT 配置的已发生预热，检查自由选择前史对差距的影响。"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from orchestration.budget_model import Budget
from orchestration.path_reference import Opportunity, Phase, Policy, replay
from orchestration.trace_admission import Demand, solve

SOURCES = ['experiments/check_admission_history.py', 'src/orchestration/trace_admission.py',
           'src/orchestration/path_reference.py', 'src/orchestration/budget_model.py',
           'src/orchestration/model.py', 'src/orchestration/metrics.py']


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = json.loads((args.run / 'manifest.json').read_text())
    for name in ['frontier.json', 'inputs.json']:
        if source['status'] != 'passed' or hashlib.sha256((args.run / name).read_bytes()).hexdigest() != source['artifact_sha256'][name]:
            raise ValueError('来源状态或摘要错误')
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    manifest = {'status': 'running', 'run_id': out.name, 'start_utc': datetime.now(timezone.utc).isoformat(),
                'software': platform.python_version(), 'host_load_start': list(os.getloadavg()), 'random_seeds': [],
                'scope': '固定同一候选配置的预热，给定前史的条件上界，不证明任意前史下最优',
                'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
                'input_sha256': {name: hashlib.sha256((args.run / name).read_bytes()).hexdigest() for name in ['frontier.json', 'inputs.json']}}
    for name in SOURCES:
        target = out / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    dump(out / 'manifest.json', manifest)
    start = time.monotonic()
    try:
        inputs = json.loads((args.run / 'inputs.json').read_text())
        phases = [Phase(**{**p, 'deadlines_us': tuple(p['deadlines_us'])}) for p in inputs['phases']]
        tasks = [Demand(**t) for t in inputs['tasks']]
        result = []
        for row in json.loads((args.run / 'frontier.json').read_text()):
            packets = [Opportunity(**{**p, **{k: tuple(p[k]) for k in ('path', 'links', 'gate_us', 'generated_us')},
                                      'capacity': row['capacity']}) for p in inputs['packets']]
            native = row['native_best']
            early, late = [Policy({(v['node'], v['flow']): v['threshold_us'] for v in native[k]}) for k in ('early', 'late')]
            selected = replay(packets, [(-800000, early), (1200000, late)])['selected']
            history = {p.packet_id: selected[p.packet_id] for p in packets if p.generated_us[-1] <= phases[0].start_us}
            budget = Budget(tuple(row['budget']['inband_per_link_bytes']), row['budget']['export_bytes'])
            answer = solve(tasks, packets, phases, budget, locked_history=history, time_limit=30)
            if answer['status'] != 'optimal' or answer['admitted_weight'] < native['admitted_weight']:
                raise RuntimeError('给定前史上界未证明最优或低于该可行候选')
            result.append({'capacity': row['capacity'], 'budget': asdict(budget), 'native_weight': native['admitted_weight'],
                           'free_history_weight': row['oracle']['admitted_weight'],
                           'locked_history_weight': answer['admitted_weight'], 'locked_history': history,
                           'history_freedom_weight_difference': row['oracle']['admitted_weight'] - answer['admitted_weight'],
                           'conditional_gap_weight': answer['admitted_weight'] - native['admitted_weight'], 'oracle': answer})
        dump(out / 'results.json', result)
        summary = {'points': len(result), 'history_freedom_changes_weight_points': sum(r['history_freedom_weight_difference'] > 0 for r in result),
                   'conditional_gap_closed_points': sum(r['conditional_gap_weight'] == 0 for r in result),
                   'remaining_positive_conditional_gap_points': sum(r['conditional_gap_weight'] > 0 for r in result),
                   'largest_history_weight_difference': max(r['history_freedom_weight_difference'] for r in result),
                   'control_costs_included': False, 'future_known': True,
                   'interpretation': '固定的是每点有界调参最佳候选自己的前史；剩余差距仍含逐包自由度和门控放宽'}
        dump(out / 'summary.json', summary)
        manifest.update(status='passed', end_utc=datetime.now(timezone.utc).isoformat(),
                        duration_seconds=time.monotonic() - start, host_load_end=list(os.getloadavg()),
                        artifact_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob('*.json') if p.name != 'manifest.json'})
        dump(out / 'manifest.json', manifest)
        print(json.dumps(summary, ensure_ascii=False))
    except Exception as exc:
        manifest.update(status='failed', error=str(exc), end_utc=datetime.now(timezone.utc).isoformat())
        dump(out / 'manifest.json', manifest)
        raise


if __name__ == '__main__':
    main()
