"""分别调参五个原有逐流索引，避免共同出口的三个流被同一阈值限制。"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'experiments')]
from orchestration.path_reference import Opportunity, Phase
from orchestration.trace_admission import Demand
from run_admission_reference import dump, native_grid

SOURCES = ['experiments/check_admission_flow_indices.py', 'experiments/run_admission_reference.py',
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
                'scope': '固定封装、已知未来的原有逐流 TT-INT 适配，控制成本未计，尚未执行网络检查'}
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
        dump(out / 'grid.json', {'early_phase_shapes': 8, 'early_branch_thresholds': [1, None],
                                 'early_shared_flow1_and2': None, 'early_shared_flow3': [1, None],
                                 'late_branch_thresholds': [1, 100001, 200001, 300001, 500001, None],
                                 'late_shared_each_flow_thresholds': [1, 100001, 200001, None],
                                 'initialization': '与 initialization-01 相同的逐节点启用时刻，不重置门控',
                                 'control_cost_measured': False, 'source_ratio': 1,
                                 'trial_count_per_capacity': 18432,
                                 'tuning_uses_whole_known_trace': True})
        rows = []
        for capacity in (1, 2):
            candidates = native_grid([replace(p, capacity=capacity) for p in packets], phases, tasks,
                                     delayed_initialization=True,
                                     late_branch=(1, 100001, 200001, 300001, 500001, None), independent_shared=True)
            with gzip.open(out / f'candidates-capacity-{capacity}.jsonl.gz', 'wt', encoding='utf-8') as f:
                for r in candidates:
                    f.write(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n')
            for point in (r for r in frontier if r['capacity'] == capacity):
                budget = point['budget']
                feasible = [r for r in candidates if all(v <= b for v, b in zip(r['inband_per_link_bytes'], budget['inband_per_link_bytes']))
                            and r['export_bytes'] <= budget['export_bytes']]
                best = min(feasible, key=lambda r: (-r['admitted_weight'], r['data_total_byte_hops']))
                gap = point['oracle']['admitted_weight'] - best['admitted_weight']
                if gap < 0:
                    raise RuntimeError('原有调参候选超过固定封装上界')
                rows.append({'capacity': capacity, 'budget': budget, 'native_best': best,
                             'upper_weight': point['oracle']['admitted_weight'], 'admission_weight_gap': gap})
            print('independent flow indices', capacity, len(candidates), flush=True)
        dump(out / 'results.json', rows)
        summary = {'points': len(rows), 'candidate_trials': 36864,
                   'closed_points': sum(r['admission_weight_gap'] == 0 for r in rows),
                   'positive_gap_points': sum(r['admission_weight_gap'] > 0 for r in rows),
                   'largest_weight_gap': max(r['admission_weight_gap'] for r in rows),
                   'control_costs_included': False, 'network_deployment_verified': False,
                   'conclusion': '原有 TT-INT 的资格初始化和逐流调参需作为强对照，模型接纳不等于完整预算可部署'}
        dump(out / 'summary.json', summary)
        manifest.update(status='passed', duration_seconds=time.monotonic() - start,
                        end_utc=datetime.now(timezone.utc).isoformat(), host_load_end=list(os.getloadavg()),
                        artifact_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()
                                         if p.is_file() and p.name != 'manifest.json'})
        dump(out / 'manifest.json', manifest)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    except Exception as exc:
        manifest.update(status='failed', error=str(exc), end_utc=datetime.now(timezone.utc).isoformat())
        dump(out / 'manifest.json', manifest)
        raise


if __name__ == '__main__':
    main()
