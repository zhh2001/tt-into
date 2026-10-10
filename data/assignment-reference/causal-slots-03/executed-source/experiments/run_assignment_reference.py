"""准确分配模型的独立穷举审计，以及训练、保留种子上的因果事件适配。"""
import argparse
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import gzip
from itertools import product
import json
import os
from pathlib import Path
import platform
import random
import shutil
import sys
import time
import traceback

import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from orchestration.assignment_reference import Dependency, Flow, Item, solve_dinto, solve_intopp
from orchestration.path_reference import Opportunity, Phase, Policy, replay, violations
from orchestration.slot_reference import (START, END, SLOT_US, EFFECT_DELAY_US, WEIGHTS,
                                         decision_times, run_slot_reference)

SOURCES = ['experiments/run_assignment_reference.py', 'src/orchestration/assignment_reference.py',
           'src/orchestration/slot_reference.py', 'src/orchestration/path_reference.py',
           'src/orchestration/deployment.py', 'src/orchestration/metrics.py',
           'src/orchestration/model.py', 'src/orchestration/budget_model.py']
CALIBRATION = 'data/admission-validation/admission-margin-ordinary-01'
SCENARIOS = ('jitter', 'burst', 'low_traffic', 'deadline_change', 'report_delay', 'report_loss', 'budget_shortfall')
TRAIN_SEEDS, TEST_SEEDS = tuple(range(8)), tuple(range(100, 120))


def dump(path, value):
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    if path.suffix == '.gz':
        path.write_bytes(gzip.compress(payload, mtime=0))
    else:
        path.write_bytes(payload)


def rows(assignment):
    return [{'node': n, 'kind': k, 'flow': f} for (n, k), f in sorted(assignment.items())]


def brute_force(items, flows, previous, queue=None, tradeoff=None, dependencies=(), distinct=False):
    """独立目标和约束计算，不使用被审计的枚举器、Delta 或依赖函数。"""
    best, trials = None, 0
    for choices in product(*[[0] + [f.flow for f in flows if i.node in f.path] for i in items]):
        trials += 1
        if any(sum(i.size_bytes for i, chosen in zip(items, choices) if chosen == f.flow) > f.capacity_bytes for f in flows):
            continue
        a = {i.key: chosen for i, chosen in zip(items, choices) if chosen}
        delta = sum(i.size_bytes * abs(int(a.get(i.key) == f.flow) - int(previous.get(i.key) == f.flow))
                    for i in items for f in flows)
        if queue is not None:
            score = queue * delta - tradeoff * sum(i.gain for i in items if i.key in a)
        else:
            score = 0
            for d in dependencies:
                picked = [(n, k) for n, k in a if n in d.nodes and k in d.kinds]
                count = len({k for _, k in picked}) if distinct and not d.spatial else len(picked)
                if d.active and count >= len(d.kinds):
                    score -= d.weight
        candidate = (score, delta, tuple(a.get(i.key, 0) for i in sorted(items, key=lambda x: x.key)), a)
        if best is None or candidate[:3] < best[:3]:
            best = candidate
    return best, trials


def audit_models():
    rng = random.Random(715)
    audits, solver_calls, brute_trials = [], 0, 0
    for number in range(60):
        items = [Item(n, k, rng.randrange(1, 5), rng.choice((0., .5, 2., 7.)))
                 for n, k in ((1, 1), (1, 2), (2, 1), (2, 2))]
        flows = [Flow(1, (1, 2), rng.randrange(0, 10)), Flow(2, (2, 3), rng.randrange(0, 8))]
        previous = {(2, 1): rng.choice((1, 2))} if rng.random() < .5 else {}
        q, v, average = rng.choice((0., .5, 3., 20.)), rng.choice((1., 4., 50.)), rng.choice((0., 2., 4.))
        deps = [Dependency('spatial-1', (1,), (1, 2), True, weight=2),
                Dependency('spatial-2', (2,), (1, 2), True, weight=1),
                Dependency('temporal', (1, 2), (1, 2), False, active=rng.choice((True, False)), weight=4)]
        result_rows = []
        for name in ('dinto', 'intopp_count', 'intopp_distinct'):
            expected, tries = brute_force(items, flows, previous, q if name == 'dinto' else None,
                                          v if name == 'dinto' else None, deps, name == 'intopp_distinct')
            brute_trials += tries
            result = solve_dinto(items, flows, previous, q, v, average) if name == 'dinto' else solve_intopp(
                items, flows, deps, previous, temporal='distinct' if name == 'intopp_distinct' else 'count')
            solver_calls += result['solver_calls']
            actual = result['p3_objective'] if name == 'dinto' else -result['dependency_score']
            if result['assignment'] != expected[3] or abs(actual - expected[0]) > 1e-7:
                raise RuntimeError(f'独立穷举与 MILP 不一致: instance={number}, {name}')
            if name == 'dinto' and result['next_queue'] != max(0, q + expected[1] - average):
                raise RuntimeError('虚拟队列递推不一致')
            result = dict(result, assignment=rows(result['assignment']))
            result_rows.append({'model': name, 'result': result, 'brute_assignment_trials': tries,
                                'brute_objective': expected[0], 'matched': True})
        audits.append({'instance': number, 'items': [asdict(i) for i in items], 'flows': [asdict(f) for f in flows],
                       'previous': rows(previous), 'queue': q, 'tradeoff': v, 'average_change_bytes': average,
                       'dependencies': [asdict(d) for d in deps], 'results': result_rows})
    return {'instances': audits, 'model_comparisons': len(audits) * 3,
            'milp_calls_including_ties': solver_calls, 'brute_assignment_trials': brute_trials}


def runtime_cost():
    directory = ROOT / CALIBRATION
    raw = [json.loads(line) for line in (directory / 'rpc-operations.jsonl').read_text().splitlines()]
    case = json.loads((directory / 'point-10/result.json').read_text())
    start, end = case['installation_rpc_indices']
    installation = raw[start:end]
    by_register = defaultdict(set)
    for r in raw:
        if r['method'] == 'bm_register_write':
            by_register[r['arguments'][0]].add(r['request_serialized_bytes'] + r['reply_serialized_bytes'])
    names = ['TTEgress.time_threshold', 'TTEgress.max_count']
    if len(installation) != 69 or any(len(by_register[name]) != 1 for name in names):
        raise ValueError('运行时 API 校准记录不满足共同安装及固定长度前提')
    return {'installation_bytes': sum(r['request_serialized_bytes'] + r['reply_serialized_bytes'] for r in installation),
            'installation_calls': len(installation), 'write_bytes': {name: next(iter(by_register[name])) for name in names},
            'basis': 'modeled_serialized_rpc_request_plus_reply', 'source_directory': CALIBRATION,
            'source_sha256': {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                              for name in ('rpc-operations.jsonl', 'point-10/result.json')},
            'scope': '状态安装、初次启用、后续寄存器变更；被动报告在导出费中；路由 CLI、TCP、握手和主机进程未计入'}


def make_workload(scenario, seed):
    if scenario not in SCENARIOS:
        raise ValueError('未知扰动场景')
    rng = random.Random(seed)
    phased = scenario == 'deadline_change'
    phases = [Phase(0, 1220000, (150000, 150000, 250000)), Phase(1220000, END, (650000, 650000, 175000))] if phased else [
        Phase(0, END, (220000, 220000, 260000))]
    notifications = decision_times(phases)

    def guard(t):
        # 给定空隙保证固定 2 ms 生效模型没有在途切换，不由算法改变业务。
        for epoch in notifications:
            if epoch - 100 <= t < epoch + 5000:
                t = epoch + 5000
        return max(START + 5000, t)

    packets = []
    for flow, path, links, offset, period in ((1, (1, 3), (0, 2), 0, 100000),
                                             (2, (2, 3), (1, 2), 50000, 100000),
                                             (3, (3,), (2,), 25000, 200000)):
        times = []
        if scenario == 'burst':
            for base in range(START + offset, END, 400000 if flow != 3 else 800000):
                times.extend(base + i * 5000 + rng.randrange(-1000, 1001) for i in range(4))
        else:
            for t in range(START + offset, END, period if scenario != 'low_traffic' or flow != 3 else 600000):
                if scenario == 'low_traffic' and flow == 1 and 400000 <= t < 1000000:
                    continue
                times.append(t + rng.randrange(-20000, 20001))
        for t in sorted(set(guard(t) for t in times)):
            if t >= END:
                continue
            gates = tuple(t + i * 20 for i in range(len(path)))
            generated = tuple(t + 5 for t in gates)
            delay = rng.randrange(60000, 140001) if scenario == 'report_delay' else rng.randrange(100, 501)
            arrival = generated[-1] + delay
            if scenario == 'report_loss' and rng.random() < .15:
                arrival = None
            packets.append(Opportunity(0, flow, path, links, gates, generated, arrival))
    packets.sort(key=lambda p: (p.gate_us[0], p.flow))
    packets = [replace(p, packet_id=i) for i, p in enumerate(packets, 1)]
    tight = scenario == 'budget_shortfall'
    budget = {'inband_per_link_bytes': [1024, 1024, 800 if tight else 1600],
              'export_bytes': 2400 if tight else 4800, 'runtime_api_serialized_bytes': 12000 if tight else 20000}
    # 有利放宽的及时报告机会检查：不限制项数、预算或门控。它仅给必要上界。
    all_policy = Policy({(1, 1): 1, (2, 2): 1, (3, 1): 1, (3, 2): 1, (3, 3): 1})
    relaxed = replay([replace(p, capacity=2) for p in packets], [(START, all_policy)])
    opportunity_bad = violations(relaxed['samples'], phases)
    return {'packets': packets, 'phases': phases, 'budget': budget,
            'relaxed_opportunity_fulfilled_nodes': [n for n in (1, 2, 3) if all(row[n - 1] <= 1e-10 for row in opportunity_bad)]}


def parameter_grid():
    rules = ('always', 'half', 'deadline', 'margin', 'off')
    return {'tt': [{'branch1': a, 'branch2': b, 'shared': c}
                   for a, b, c in product(rules, rules, rules + ('flow1', 'flow2', 'flow3'))],
            'dinto': [{'tradeoff': v, 'average_change_bytes': d, 'gain': g}
                      for v, d, g in product((1., 10., 100., 1000.), (0., 12., 24., 48.), ('static', 'age'))],
            'intopp': [{'lookahead_us': 0}],
            'intopp_ahead': [{'lookahead_us': t} for t in (50000, 100000, 200000, 300000)]}


def summarize(results):
    return {'runs': len(results), 'budget_satisfied_runs': sum(r['budget_satisfied'] for r in results),
            'all_task_fulfilled_runs': sum(r['fulfilled_weight'] == 5 for r in results),
            'budget_and_all_task_fulfilled_runs': sum(r['budget_satisfied'] and r['fulfilled_weight'] == 5 for r in results),
            'mean_budget_qualified_weight': sum(r['budget_qualified_weight'] for r in results) / len(results),
            'mean_weighted_violation_fraction': sum(r['weighted_violation_fraction'] for r in results) / len(results),
            'mean_data_total_byte_hops': sum(r['data_total_byte_hops'] for r in results) / len(results),
            'mean_runtime_api_serialized_bytes': sum(r['runtime_api_total_serialized_bytes'] for r in results) / len(results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    manifest = {'status': 'running', 'run_id': out.name, 'start_utc': datetime.now(timezone.utc).isoformat(),
                'scope': '准确原模型与条件事件适配，不是 BMv2 测量、完整论文复现或新增算法比较',
                'software': {'python': platform.python_version(), 'scipy': scipy.__version__, 'solver': 'scipy.optimize.milp/HiGHS'},
                'host_load_start': list(os.getloadavg()),
                'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}}
    for name in SOURCES:
        target = out / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    dump(out / 'manifest.json', manifest)
    try:
        calibration = runtime_cost()
        dump(out / 'runtime-api-calibration.json', calibration)
        grid = parameter_grid()
        # 在任何目标评价之前保存全部轨迹、种子和网格。测试数据不参与参数选择。
        spec = {'scenarios': SCENARIOS, 'training_seeds': TRAIN_SEEDS, 'heldout_seeds': TEST_SEEDS,
                'capacities': [1, 2], 'grid': grid, 'warmup_us': [START, 0], 'measurement_us': [0, END],
                'slot_us': SLOT_US, 'fixed_effect_delay_us': EFFECT_DELAY_US,
                'traffic_guard_us': [-100, 5000], 'state_record_bytes': 12, 'envelope_bytes': 4,
                'source_ratio': 1, 'weights': WEIGHTS, 'loss_probability': .15,
                'training_selection': '每容量、每方法跨全部训练场景：最大平均预算内履约权重，再最小违约、数据费用、API费用、网格索引',
                'admission_promised_before_execution': False, 'runtime_api_budget_enforced_online': False,
                'assignment_capacity_adaptation': '每流 B=12*K，不包含包速率、4 B 包头或逐链路费用，另在整次运行评价',
                'dinto_gain_adaptation': 'static 为任务权重，age 为基于已收报告的管理员收益变体，不含熵学习',
                'intopp_task_adaptation': '三个单项应用，无在线聚类；H 为已收报告的经过时间，提前量仅在强变体使用',
                'budget_cost_basis': '带内/导出为不含FCS的帧增量；运行时API为校准的请求加回复，分列不相加',
                'missing_network_costs': ['固定路由CLI安装', 'TCP握手与帧开销', '主机进程和处理时间'],
                'fixed_effect_delay_is_measured': False}
        dump(out / 'specification.json', spec)
        workloads = {}
        public_inputs = []
        for split, seeds in (('training', TRAIN_SEEDS), ('heldout', TEST_SEEDS)):
            for scenario, seed in product(SCENARIOS, seeds):
                workload = make_workload(scenario, seed)
                workloads[split, scenario, seed] = workload
                public_inputs.append({'split': split, 'scenario': scenario, 'seed': seed,
                                      'packets': [asdict(p) for p in workload['packets']],
                                      'phases': [asdict(p) for p in workload['phases']], 'budget': workload['budget'],
                                      'relaxed_opportunity_fulfilled_nodes': workload['relaxed_opportunity_fulfilled_nodes']})
        dump(out / 'workloads.json.gz', public_inputs)
        audit = audit_models()
        dump(out / 'model-audit.json', audit)
        print('native model comparisons', audit['model_comparisons'], flush=True)
        chosen, training, heldout, executions = [], [], [], 0
        for capacity in (1, 2):
            for family, candidates in grid.items():
                scored = []
                for index, parameters in enumerate(candidates):
                    results = []
                    for scenario, seed in product(SCENARIOS, TRAIN_SEEDS):
                        w = workloads['training', scenario, seed]
                        r = run_slot_reference(w['packets'], w['phases'], family, parameters, capacity, calibration, w['budget'])
                        executions += 1
                        results.append(r)
                        training.append({'capacity': capacity, 'family': family, 'parameter_index': index,
                                         'scenario': scenario, 'seed': seed, 'result': r})
                    aggregate = summarize(results)
                    key = (-aggregate['mean_budget_qualified_weight'], aggregate['mean_weighted_violation_fraction'],
                           aggregate['mean_data_total_byte_hops'], aggregate['mean_runtime_api_serialized_bytes'], index)
                    scored.append((key, index, aggregate))
                _, index, aggregate = min(scored)
                parameters = candidates[index]
                chosen.append({'capacity': capacity, 'family': family, 'parameter_index': index,
                               'parameters': parameters, 'training_summary': aggregate})
                for scenario, seed in product(SCENARIOS, TEST_SEEDS):
                    w = workloads['heldout', scenario, seed]
                    r = run_slot_reference(w['packets'], w['phases'], family, parameters, capacity, calibration, w['budget'], detailed=True)
                    executions += 1
                    heldout.append({'capacity': capacity, 'family': family, 'parameter_index': index,
                                    'scenario': scenario, 'seed': seed,
                                    'relaxed_opportunity_fulfilled_nodes': w['relaxed_opportunity_fulfilled_nodes'], 'result': r})
                print('capacity', capacity, family, 'parameters', index, 'executions', executions, flush=True)
        dump(out / 'training-results.json.gz', training)
        dump(out / 'selected-parameters.json', chosen)
        dump(out / 'heldout-results.json.gz', heldout)
        grouped = []
        for capacity, family, scenario in product((1, 2), grid, SCENARIOS):
            entries = [r for r in heldout if r['capacity'] == capacity and r['family'] == family and r['scenario'] == scenario]
            grouped.append({'capacity': capacity, 'family': family, 'scenario': scenario,
                            'relaxed_all_task_opportunity_runs': sum(len(r['relaxed_opportunity_fulfilled_nodes']) == 3 for r in entries),
                            **summarize([r['result'] for r in entries])})
        summary = {'model_comparisons': audit['model_comparisons'], 'milp_calls_including_ties': audit['milp_calls_including_ties'],
                   'brute_assignment_trials': audit['brute_assignment_trials'], 'training_executions': len(training),
                   'heldout_executions': len(heldout), 'event_executions': executions, 'groups': grouped,
                   'novelty_established': False, 'network_execution_in_this_run': False,
                   'interpretation': '预先声明网格的独立种子模型诊断，不能由最优适配或弱基线差距推出新贡献'}
        dump(out / 'summary.json', summary)
        manifest.update(status='passed', end_utc=datetime.now(timezone.utc).isoformat(),
                        duration_seconds=time.monotonic() - started, host_load_end=list(os.getloadavg()),
                        artifact_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()
                                         if p.is_file() and p.name != 'manifest.json'})
        dump(out / 'manifest.json', manifest)
        print('passed', executions, 'event executions', flush=True)
    except Exception as exc:
        manifest.update(status='failed', error=str(exc), traceback=traceback.format_exc(),
                        end_utc=datetime.now(timezone.utc).isoformat(), duration_seconds=time.monotonic() - started)
        dump(out / 'manifest.json', manifest)
        raise


if __name__ == '__main__':
    main()
