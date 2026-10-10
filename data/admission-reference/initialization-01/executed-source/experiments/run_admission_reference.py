"""固定分支的资源敏感性和接纳上界，不是在线策略性能实验。"""
import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
from itertools import product
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import time
import traceback

import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from orchestration.budget_model import Budget, ControlEvent, FORMATS
from orchestration.path_reference import Opportunity, Phase, Policy, fork_packets, replay, validate, violations
from orchestration.trace_admission import Demand, certificates, solve

SOURCES = ['experiments/run_admission_reference.py', 'src/orchestration/trace_admission.py',
           'src/orchestration/path_reference.py', 'src/orchestration/budget_model.py',
           'src/orchestration/model.py', 'src/orchestration/metrics.py']


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def describe(policy):
    return [{'node': n, 'flow': f, 'threshold_us': v} for (n, f), v in sorted(policy.thresholds.items())]


def native_grid(packets, phases, tasks, delayed_initialization=False, late_branch=None):
    # 五个逐流索引都保留，但共享节点三个流使用同一阈值。
    # 这是明确的有界调参族，不宣称枚举了任意 TT-INT 控制器。
    branch = (1, 100001, 200001, 300001, None)
    shared = (1, 100001, None)
    policies = [Policy({(1, 1): a, (2, 2): b, (3, 1): c, (3, 2): c, (3, 3): c})
                for a, b, c in product(branch, branch, shared)]
    later = policies if late_branch is None else [
        Policy({(1, 1): a, (2, 2): b, (3, 1): c, (3, 2): c, (3, 3): c})
        for a, b, c in product(late_branch, late_branch, shared)]
    candidates = []
    for early, late in product(policies, later):
        if delayed_initialization:
            cold = dict.fromkeys(early.thresholds, None)
            shared_start = {**cold, (3, 3): early.thresholds[3, 3]}
            branch1_start = {**shared_start, (1, 1): early.thresholds[1, 1]}
            branch2_start = {**branch1_start, (2, 2): early.thresholds[2, 2]}
            schedule = [(-800000, Policy(cold)), (-175001, Policy(shared_start)),
                        (-100001, Policy(branch1_start)), (-50001, Policy(branch2_start)),
                        (0, early), (1200000, late)]
        else:
            schedule = [(-800000, early), (1200000, late)]
        result = replay(packets, schedule)
        bad = violations(result['samples'], phases)
        accepted = [t.node for t in tasks if all(row[t.node - 1] <= 1e-10 for row in bad)]
        candidates.append({'accepted_nodes': accepted, 'admitted_weight': sum(t.weight for t in tasks if t.node in accepted),
                           'inband_per_link_bytes': result['inband_per_link_bytes'], 'export_bytes': result['export_bytes'],
                           'data_total_byte_hops': result['data_total_byte_hops'],
                           'early': describe(early), 'late': describe(late)})
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = {'run_id': out.name, 'status': 'running', 'start_utc': datetime.now(timezone.utc).isoformat(),
                'software': {'python': platform.python_version(), 'scipy': scipy.__version__, 'solver': 'scipy.optimize.milp/HiGHS'},
                'host_load_start': list(os.getloadavg()), 'random_seeds': [],
                'scope': '未来已知事件轨迹的资源敏感性，不是网络测量或在线性能比较',
                'source_sha256': {s: hashlib.sha256((ROOT / s).read_bytes()).hexdigest() for s in SOURCES}}
    for name in SOURCES:
        target = out / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    dump(out / 'manifest.json', manifest)
    started = time.monotonic()
    try:
        tasks = [Demand(1, 2), Demand(2, 2), Demand(3, 1)]
        phases = [Phase(0, 1220000, (150000, 150000, 250000)), Phase(1220000, 2400000, (650000, 650000, 175000))]
        packets = fork_packets()
        budget_shared, budget_export = [640, 800, 960, 1120, 1280, 1600, 2048], [1200, 2400, 3600, 5000]
        dump(out / 'inputs.json', {'tasks': [asdict(t) for t in tasks], 'phases': [asdict(p) for p in phases],
                                  'packets': [asdict(p) for p in packets], 'warmup_us': [-800000, 0],
                                  'source_mode': 'fixed', 'format': 'prefix', 'capacity_options': [1, 2],
                                  'inband_branch_budget_bytes': [1024, 1024], 'shared_budget_bytes': budget_shared,
                                  'export_budget_bytes': budget_export, 'control_budget_optimized': False,
                                  'budget_axis_reason': '预先声明的灵敏度网格，包含先前正确性预算的容量轴；不按结果删选',
                                  'native_grid': {'per_phase_policies': 75, 'phase_pairs': 5625,
                                                  'branch_threshold_us': [1, 100001, 200001, 300001, None],
                                                  'shared_threshold_us': [1, 100001, None], 'source_ratio': 1,
                                                  'shared_three_flows_use_same_threshold': True,
                                                  'change_effective_us': 1200000, 'threshold_state_reset': False,
                                                  'full_future_known_in_tuning': True,
                                                  'tuning_includes_entire_warmup_and_measurement': True}})
        frontier, native_summary = [], []
        solver_calls = 0
        for capacity in (1, 2):
            trace = [replace(p, capacity=capacity) for p in packets]
            validate(trace, phases, [(-800000, Policy(dict.fromkeys([(1, 1), (2, 2), (3, 1), (3, 2), (3, 3)], 1)))])
            candidates = native_grid(trace, phases, tasks)
            dump(out / f'native-capacity-{capacity}.json', candidates)
            native_summary.append({'capacity': capacity, 'phase_pair_count': len(candidates),
                                   'all_task_fulfilled_pairs': sum(r['admitted_weight'] == 5 for r in candidates)})
            for shared, exported in product(budget_shared, budget_export):
                budget = Budget((1024, 1024, shared), exported)
                answer = solve(tasks, trace, phases, budget, time_limit=30)
                solver_calls += 1
                feasible = [r for r in candidates if all(v <= b for v, b in zip(r['inband_per_link_bytes'], budget.inband_per_link_bytes))
                            and r['export_bytes'] <= budget.export_bytes]
                native = min(feasible, key=lambda r: (-r['admitted_weight'], r['data_total_byte_hops'])) if feasible else None
                if answer['status'] != 'optimal':
                    raise RuntimeError(f'接纳上界未证明最优: K={capacity}, budget={budget}, {answer["status"]}')
                gap = answer['admitted_weight'] - (native['admitted_weight'] if native else 0)
                if gap < 0:
                    raise RuntimeError('已执行事件参照超过无门控限制上界，须检查模型')
                frontier.append({'capacity': capacity, 'budget': asdict(budget), 'oracle': answer,
                                 'native_best': native, 'admission_weight_gap': gap,
                                 'admission_upper_bound_attained_by_grid': gap == 0,
                                 'interpretation': '两者均未来已知且不含控制优化；上界逐包选择不可免费部署，非新增收益'})
            print('capacity', capacity, 'native pairs', len(candidates), 'solver calls', solver_calls, flush=True)
        dump(out / 'frontier.json', frontier)
        # 独立诊断输入把真实机会、报告延迟、MTU 和同时容量不足分开。
        tiny_phase = [Phase(0, 5, (10, 10, 10))]
        collision = Opportunity(1, 1, (1, 3), (0, 2), (-2, -1), (-2, -1), 0)
        scenarios = [('single_packet_capacity1', [Demand(1, 2), Demand(3)], [collision], tiny_phase, {}),
                     ('single_packet_capacity2', [Demand(1, 2), Demand(3)], [replace(collision, capacity=2)], tiny_phase, {}),
                     ('mtu', [Demand(3)], [replace(collision, ip_bytes=1485)], tiny_phase, {}),
                     ('late_report', [Demand(1, initial_generated_us=0)],
                      [Opportunity(1, 1, (1,), (0,), (4,), (4,), 9)], [Phase(0, 8, (5, 5, 5))], {}),
                     ('lost_report', [Demand(1, initial_generated_us=0)],
                      [Opportunity(1, 1, (1,), (0,), (4,), (4,), None)], [Phase(0, 8, (5, 5, 5))], {}),
                     ('time_gate_conflict', [Demand(1, initial_generated_us=0)],
                      [Opportunity(i, 1, (1,), (0,), (t,), (t,), t) for i, t in enumerate((4, 8), 1)],
                      [Phase(0, 12, (5, 5, 5))], {'thresholds': {(1, 1): 5}})]
        diagnostics = []
        for name, demand, trace, windows, options in scenarios:
            solved = solve(demand, trace, windows, **options)
            forced = solve(demand, trace, windows, force_all=True, **options)
            solver_calls += 2
            diagnostics.append({'name': name, 'tasks': [asdict(t) for t in demand], 'packets': [asdict(p) for p in trace],
                                'phases': [asdict(p) for p in windows],
                                'thresholds': [{'node': n, 'flow': f, 'threshold_us': v} for (n, f), v in options.get('thresholds', {}).items()],
                                'certificates': certificates(demand, trace, windows), 'admission': solved,
                                'force_all': forced})
        control = ControlEvent('installation', -10, -1, (20,), 'declared synthetic fixture', 'serialized_rpc')
        controlled = solve([Demand(1, initial_generated_us=0)],
                           [Opportunity(1, 1, (1,), (0,), (1,), (1,), 2)], [Phase(0, 4, (5, 5, 5))],
                           Budget((100, 100, 100), 100, (19,)), control_events=[control])
        solver_calls += 1
        dump(out / 'diagnostics.json', {'cases': diagnostics, 'fixed_control_budget_case': controlled,
                                       'control_event': asdict(control), 'control_budget_bytes': [19]})
        summary = {'frontier_points': len(frontier), 'solve_api_calls': solver_calls,
                   'native_policy_pair_trials': sum(r['phase_pair_count'] for r in native_summary),
                   'native_summary': native_summary,
                   'closed_admission_points': sum(r['admission_weight_gap'] == 0 for r in frontier),
                   'positive_gap_points': sum(r['admission_weight_gap'] > 0 for r in frontier),
                   'largest_gap_weight': max(r['admission_weight_gap'] for r in frontier),
                   'all_tasks_possible_points': sum(r['oracle']['admitted_weight'] == 5 for r in frontier),
                   'zero_admission_points': sum(r['oracle']['admitted_weight'] == 0 for r in frontier),
                   'control_costs_in_frontier': False, 'deployment_feasibility_established': False,
                   'conclusion': '资源与及时收报可限制接纳；离线逐包自由度和有界调参差距不证明 TT-INTO 新贡献'}
        dump(out / 'summary.json', summary)
        manifest.update(status='passed', duration_seconds=time.monotonic() - started,
                        end_utc=datetime.now(timezone.utc).isoformat(), host_load_end=list(os.getloadavg()),
                        artifact_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob('*.json') if p.name != 'manifest.json'})
        dump(out / 'manifest.json', manifest)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    except Exception as exc:
        manifest.update(status='failed', error=str(exc), traceback=traceback.format_exc(),
                        end_utc=datetime.now(timezone.utc).isoformat(), duration_seconds=time.monotonic() - started)
        dump(out / 'manifest.json', manifest)
        raise


if __name__ == '__main__':
    main()
