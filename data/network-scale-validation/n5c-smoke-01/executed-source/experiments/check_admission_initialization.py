"""检查原有逐流禁用、临近窗口启用和晚期阈值扩展是否消除接纳差距。"""
import argparse
from dataclasses import replace
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
sys.path.insert(0, str(ROOT / 'experiments'))
from orchestration.path_reference import Opportunity, Phase
from orchestration.trace_admission import Demand
from run_admission_reference import dump, native_grid

SOURCES = ['experiments/check_admission_initialization.py', 'experiments/run_admission_reference.py',
           'src/orchestration/path_reference.py', 'src/orchestration/trace_admission.py',
           'src/orchestration/model.py', 'src/orchestration/metrics.py', 'src/orchestration/budget_model.py']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = json.loads((args.run / 'manifest.json').read_text())
    for name in ('inputs.json', 'frontier.json'):
        if source['status'] != 'passed' or hashlib.sha256((args.run / name).read_bytes()).hexdigest() != source['artifact_sha256'][name]:
            raise ValueError('来源摘要或状态不合法')
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    manifest = {'run_id': out.name, 'status': 'running', 'start_utc': datetime.now(timezone.utc).isoformat(),
                'software': platform.python_version(), 'host_load_start': list(os.getloadavg()), 'random_seeds': [],
                'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
                'input_sha256': {name: hashlib.sha256((args.run / name).read_bytes()).hexdigest() for name in ('inputs.json', 'frontier.json')},
                'scope': '未来已知、控制费用未计的原有 TT-INT 配置事件参照，不是新增策略或网络测量'}
    for name in SOURCES:
        target = out / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    dump(out / 'manifest.json', manifest)
    try:
        inputs = json.loads((args.run / 'inputs.json').read_text())
        packets = [Opportunity(**{**p, **{k: tuple(p[k]) for k in ('path', 'links', 'gate_us', 'generated_us')}}) for p in inputs['packets']]
        phases = [Phase(**{**p, 'deadlines_us': tuple(p['deadlines_us'])}) for p in inputs['phases']]
        tasks = [Demand(**t) for t in inputs['tasks']]
        frontier = json.loads((args.run / 'frontier.json').read_text())
        dump(out / 'initialization.json', {'source_envelopes': '与原始网格一致，所有合法包固定封装，空头仍计费',
                                          'capacity_zero_initially': True, 'gate_state_reset': False,
                                          'activate_us': {'shared_flow3': -175001, 'branch1_flow1': -100001,
                                                          'branch2_flow2': -50001, 'shared_flow1_and2': 0},
                                          'demand_change_preparation_us': 1200000,
                                          'late_threshold_options_extended_us': [1, 100001, 200001, 300001, 500001, None],
                                          'extra_control_calls_measured': False,
                                          'interpretation': '已知窗口起点及业务时刻的许可初始化，不据此宣称未知业务硬保证'})
        rows, groups = [], []
        for name, extended in (('delayed_initialization', False), ('delayed_and_late_grid', True)):
            for capacity in (1, 2):
                candidates = native_grid([replace(p, capacity=capacity) for p in packets], phases, tasks,
                                         delayed_initialization=True,
                                         late_branch=(1, 100001, 200001, 300001, 500001, None) if extended else None)
                # 每次候选尝试都保留一行，未达到期限和预算的配置同样保留。
                with (out / f'{name}-capacity-{capacity}.jsonl').open('w') as f:
                    for c in candidates:
                        f.write(json.dumps(c, ensure_ascii=False, separators=(',', ':')) + '\n')
                groups.append({'name': name, 'capacity': capacity, 'trials': len(candidates)})
                for point in (r for r in frontier if r['capacity'] == capacity):
                    budget = point['budget']
                    feasible = [r for r in candidates if all(v <= b for v, b in zip(r['inband_per_link_bytes'], budget['inband_per_link_bytes']))
                                and r['export_bytes'] <= budget['export_bytes']]
                    best = min(feasible, key=lambda r: (-r['admitted_weight'], r['data_total_byte_hops']))
                    gap = point['oracle']['admitted_weight'] - best['admitted_weight']
                    if gap < 0:
                        raise RuntimeError('初始化配置超过给定固定封装上界')
                    rows.append({'family': name, 'capacity': capacity, 'budget': budget, 'best': best,
                                 'upper_weight': point['oracle']['admitted_weight'], 'admission_weight_gap': gap,
                                 'base_grid_weight': point['native_best']['admitted_weight']})
                print(name, capacity, len(candidates), flush=True)
        dump(out / 'results.json', rows)
        summary = {'candidate_trials': sum(g['trials'] for g in groups), 'groups': groups,
                   'families': [{'name': name, 'points': 56,
                                 'closed_points': sum(r['admission_weight_gap'] == 0 for r in rows if r['family'] == name),
                                 'positive_gap_points': sum(r['admission_weight_gap'] > 0 for r in rows if r['family'] == name),
                                 'largest_weight_gap': max(r['admission_weight_gap'] for r in rows if r['family'] == name)}
                                for name in ('delayed_initialization', 'delayed_and_late_grid')],
                   'control_costs_included': False, 'network_deployment_verified': False,
                   'future_known': True, 'conclusion': '初始化及原有阈值选项须先充分核对；模型差距不是新算法的等预算收益'}
        dump(out / 'summary.json', summary)
        manifest.update(status='passed', duration_seconds=time.monotonic() - start,
                        end_utc=datetime.now(timezone.utc).isoformat(), host_load_end=list(os.getloadavg()),
                        artifact_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()
                                         if p.suffix in ('.json', '.jsonl') and p.name != 'manifest.json'})
        dump(out / 'manifest.json', manifest)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    except Exception as exc:
        manifest.update(status='failed', error=str(exc), end_utc=datetime.now(timezone.utc).isoformat())
        dump(out / 'manifest.json', manifest)
        raise


if __name__ == '__main__':
    main()
