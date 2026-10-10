#!/usr/bin/env python3
"""穷举固定分支事件中的静态配置，核对预声明的原有 TT-INT 阈值更新。"""
import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from orchestration.path_reference import Phase, Policy, evaluate, fork_packets, replay, static_policies, validate, violations

SOURCES = ['experiments/run_path_reference.py', 'src/orchestration/path_reference.py',
           'src/orchestration/model.py', 'src/orchestration/metrics.py', 'src/orchestration/budget_model.py']


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def describe(policy):
    row = asdict(policy)
    row['thresholds'] = [{'node': n, 'flow': f, 'threshold_us': v, 'capacity': int(v is not None)}
                         for (n, f), v in policy.thresholds.items()]
    return row


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    manifest = {'run_id': out.name, 'start_utc': datetime.now(timezone.utc).isoformat(),
                'status': 'running', 'software': platform.python_version(), 'host_load_start': list(os.getloadavg()),
                'scope': '确定性事件参照及有限网格筛查，不是网络测量或新算法性能比较',
                'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}}
    for name in SOURCES:
        target = out / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    dump(out / 'manifest.json', manifest)
    packets = fork_packets()
    phases = [Phase(0, 1220000, (150000, 150000, 250000)),
              Phase(1220000, 2400000, (650000, 650000, 175000))]
    keys = [(1, 1), (2, 2), (3, 1), (3, 2), (3, 3)]
    early = Policy(dict.fromkeys(keys, 1))
    late = Policy({key: 300001 if key[0] < 3 else 1 for key in keys})
    schedule = [(-800000, early), (1200000, late)]
    validate(packets, phases, schedule)
    dump(out / 'inputs.json', {'packets': [asdict(p) for p in packets], 'phases': [asdict(p) for p in phases],
                             'task_objects': [{'switch_id': n, 'egress_port': 2, 'queue_class': 0, 'metric': 'queue_packets'} for n in (1, 2, 3)],
                             'traffic': 'flow1: branch1->shared3, flow2: branch2->shared3, flow3: shared3; 周期100/100/200 ms',
                             'warmup_us': [-800000, 0], 'report_delay_us': 100,
                             'deadline_changes_known_before_run': True})
    grid = [1, 100001, 200001, 300001, None]
    groups = []
    for name, scope, reservoir in [('tt_fixed', 'flow', False), ('shared_fixed', 'port', False), ('weighted_reservoir', 'port', True)]:
        count, fulfilled, best = 0, 0, None
        seeds = range(8) if reservoir else [None]
        for policy in static_policies(grid, scope, reservoir):
            for seed in seeds:
                result = replay(packets, [(-800000, policy)], seed=seed or 0)
                bad = violations(result['samples'], phases)
                passed = sum(v <= 1e-10 for row in bad for v in row)
                score = (-passed, sum(sum(row) for row in bad), result['data_total_byte_hops'])
                count += 1
                fulfilled += int(passed == 6)
                if best is None or score < best[0]:
                    best = (score, policy, seed, result)
        result = best[3]
        groups.append({'name': name, 'configuration_seed_trials': count, 'fully_fulfilled_trials': fulfilled,
                       'best_selection_rule': '先最多满足六个对象阶段要求，再最小违约比例之和，再最小数据费用',
                       'best_policy': describe(best[1]), 'best_seed': best[2], 'best': evaluate(result, phases),
                       'best_wire': {k: result[k] for k in ('inband_per_link_bytes', 'export_bytes', 'data_total_byte_hops')},
                       'grid_threshold_us': grid, 'source_ratio_options': [1] if reservoir else [1, 2],
                       'counter_phase_options': '所有 0..N-1，冷启动写入后保留完整预热',
                       'seed_options': list(seeds), 'seeds_are_python_reference_only': reservoir,
                       'limitations': '有限网格；蓄水池仅共享门控及 N=1；未实现完整 Dev 选流、反馈控制或通用调度'})
        print(name, count, 'fully_fulfilled', fulfilled, flush=True)
    dynamic = replay(packets, schedule, detailed=True)
    relaxed = replay([replace(p, capacity=2) for p in packets], [(-800000, early)])
    full = evaluate(dynamic, phases)
    if any(not t['fulfilled'] for phase in full for t in phase['tasks']):
        raise RuntimeError('预声明阈值更新在事件轨迹中未履约')
    dump(out / 'schedule.json', {'policy_family': 'TT-INT 原有控制器阈值调整的预声明适配',
                                'policies': [{'effective_us': t, 'policy': describe(policy)} for t, policy in schedule],
                                'updates': [{'node': 1, 'register': 'TTEgress.time_threshold', 'index': 1, 'value': 300001},
                                            {'node': 2, 'register': 'TTEgress.time_threshold', 'index': 2, 'value': 300001}],
                                'reset_gate_on_update': False, 'availability_information': '已知需求切换，无在线预测',
                                'deployment_feasibility_established': False, 'control_bytes': None,
                                'note': '本文件不伪造实际安装时刻或控制价目，网络运行另行核对'})
    dump(out / 'scheduled-events.json', dynamic)
    dump(out / 'results.json', {'static_groups': groups, 'scheduled_tt': full,
                               'capacity2_static_tt': evaluate(relaxed, phases),
                               'capacity2_static_wire': {k: relaxed[k] for k in ('inband_per_link_bytes', 'export_bytes', 'data_total_byte_hops')},
                               'scheduled_wire': {k: dynamic[k] for k in ('inband_per_link_bytes', 'export_bytes', 'data_total_byte_hops')},
                               'control_cost_included': False,
                               'joint_budget_feasibility_established': False,
                               'conclusion': 'K=1 时原有 TT-INT 调阈值即能解决；K=2 时固定阈值也能解决，不能作为新增贡献'})
    manifest.update(status='passed', end_utc=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=time.monotonic() - started, host_load_end=list(os.getloadavg()),
                    packet_count=len(packets), seeds=list(range(8)),
                    artifact_sha256={f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in out.glob('*.json') if f.name != 'manifest.json'})
    dump(out / 'manifest.json', manifest)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        if '--output' in sys.argv:
            target = Path(sys.argv[sys.argv.index('--output') + 1]) / 'manifest.json'
            if target.exists():
                record = json.loads(target.read_text())
                if record['status'] == 'running':
                    record.update(status='failed', error=str(exc), traceback=traceback.format_exc(),
                                  end_utc=datetime.now(timezone.utc).isoformat())
                    dump(target, record)
        raise
