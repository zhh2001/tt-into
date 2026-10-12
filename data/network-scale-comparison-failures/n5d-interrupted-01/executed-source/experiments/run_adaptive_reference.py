"""先静态训练与独立校准，再执行新保留种子的机会/预算消融。"""
import argparse
from datetime import datetime, timezone
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from orchestration.adaptive import AdaptiveProxy, AdaptiveSettings, history_entry, select
from orchestration.event_engine import EventEngine
from orchestration.event_metrics import evaluate_tasks, window_costs
from orchestration.event_schema import Read, Write
from orchestration.rolling import Settings, changes, excess, installation
from adaptive_workloads import CALIBRATION_SEEDS, SCENARIOS, TEST_SEEDS, TRAIN_SEEDS, generate
from rolling_rpc_cost import calibration
from rolling_workloads import MANAGEMENT, MEASUREMENT, NODES, budget_for
from run_rolling_reference import SOURCES as OLD_SOURCES, plain, simulate, train, write_gzip, write_json

METHODS = ('static', 'feedback', 'local', 'joint', 'phase_joint', 'uniform_guard', 'phase_guard', 'phase_local_guard')
GUARDED = ('uniform_guard', 'phase_guard', 'phase_local_guard')
PARAMETERS = tuple(AdaptiveSettings(gain_margin=g, reserve_fraction=r) for g, r in itertools.product((0.0, .05, .15), (0.0, .1)))
SOURCES = OLD_SOURCES + ('src/orchestration/adaptive.py', 'experiments/adaptive_workloads.py',
                         'experiments/run_adaptive_reference.py', 'experiments/check_rolling_reference.py',
                         'tests/test_adaptive.py')


def adaptive_simulate(workload, budget, trained, method, parameters):
    settings = Settings()
    if method in METHODS[:4]:
        return simulate(workload, budget, trained, method, settings)
    if method not in METHODS:
        raise ValueError('未知策略')
    temporal, guarded, local = method != 'uniform_guard', method != 'phase_joint', method == 'phase_local_guard'
    engine = EventEngine(NODES, MANAGEMENT.routes, workload.packets,
                         installation(trained, MANAGEMENT, settings), capacity=settings.capacity, notices=workload.notices)
    decisions, history = [], []
    for epoch in range(10):
        now = epoch * settings.epoch_us
        view = engine.run_until(now)
        proxy = AdaptiveProxy(view, MANAGEMENT, budget, settings, parameters, tuple(history), temporal, guarded)
        started = time.perf_counter_ns()
        selected = select(proxy, trained, local)
        elapsed = time.perf_counter_ns() - started
        diffs = changes(selected['config'], proxy.fields, MANAGEMENT)
        if len(diffs) > settings.max_update_rpcs:
            raise ValueError('每周期更新限额超出')
        writes, reads = [], []
        for ordinal, (key, field, value) in enumerate(diffs):
            node, flow = key
            issued_at = now + 1 + ordinal * 200
            write = Write(f'{method}-{epoch}-{ordinal}', node, flow, field, value, epoch + 2,
                          issued_at, issued_at + 20 + node * 10, issued_at + 100, MANAGEMENT.cost_for_write(field))
            engine.submit_write(write)
            writes.append(write)
        for ordinal, (flow, route) in enumerate(sorted(MANAGEMENT.routes.items())):
            issued_at = now + 1 + ordinal * 10
            read = Read(f'{method}-{epoch}-count-{ordinal}', flow, route[0].node, issued_at,
                        issued_at + 50, issued_at + 100, MANAGEMENT.read_cost)
            engine.submit_read(read)
            reads.append(read)
        decisions.append({'time_us': now, 'view': view, 'current_config': proxy.current, 'selected': selected,
                          'writes': writes, 'reads': reads, 'decision_elapsed_ns': elapsed,
                          'rates': [{'flow': f, 'rate_pps': proxy.rates.get(f)} for f in sorted(MANAGEMENT.routes)],
                          'profiles': proxy.profiles, 'budget_history': tuple(history),
                          'budget_diagnostic': proxy.budget_diagnostic,
                          'candidate_scores': list(proxy.memo.values()),
                          'predicted_loss_basis': '合法报告的名义相位、群和有效接收新近度；真实生成年龄仅作评价'})
        entry = history_entry(proxy, selected)
        if entry is not None:
            history.append(entry)
    result = engine.run()
    metrics = evaluate_tasks(workload.tasks, result['receipts'], settings.clip)
    cost = window_costs(result['cost_events'], *MEASUREMENT, budget.link_bytes_per_s, budget.export_bytes_per_s)
    epoch_costs = [window_costs(result['cost_events'], t * settings.epoch_us, (t + 1) * settings.epoch_us) for t in range(2, 10)]
    row = {'scenario': workload.scenario, 'seed': workload.seed, 'method': method,
           'loss': metrics['weighted_clipped_normalized_loss'], 'coverage': metrics['weighted_active_interval_coverage'],
           'cost': cost, 'excess': excess(cost, budget), 'feasible': excess(cost, budget) <= 1e-12,
           'over_budget_epoch_count': sum(excess(c, budget) > 1e-12 for c in epoch_costs),
           'update_rpc_count': sum(len(d['writes']) for d in decisions[2:]),
           'decision_elapsed_ns': [d['decision_elapsed_ns'] for d in decisions]}
    return row, {'summary': row, 'decisions': decisions, 'result': result, 'task_metrics': metrics,
                 'epoch_costs': epoch_costs, 'diagnostics': []}


def calibration_score(rows, parameters):
    return (sum(not r['feasible'] for r in rows), sum(r['excess'] for r in rows), sum(r['loss'] for r in rows) / len(rows),
            sum(r['update_rpc_count'] for r in rows), parameters.gain_margin, parameters.reserve_fraction)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run = args.output
    run.mkdir(parents=True, exist_ok=False)
    manifest = {'experiment_id': run.name, 'status': 'running', 'started_utc': datetime.now(timezone.utc).isoformat(),
                'kind': 'causal_adaptive_event_model', 'settings': Settings(), 'adaptive_parameters': PARAMETERS,
                'methods': METHODS, 'scenarios': SCENARIOS, 'budgets': ('low', 'high'),
                'train_seeds': TRAIN_SEEDS, 'calibration_seeds': CALIBRATION_SEEDS, 'test_seeds': TEST_SEEDS,
                'previous_consumed_test_seeds': (101, 211, 307, 401), 'measurement_window_us': MEASUREMENT,
                'topology': {'nodes': NODES, 'routes': [{'flow': f, 'objects': r} for f, r in MANAGEMENT.routes.items()]},
                'budget_definition': {s: {b: budget_for(s, b) for b in ('low', 'high')} for s in SCENARIOS},
                'environment': {'python': platform.python_version(), 'cpu_count': os.cpu_count(),
                                'load_average_at_start': list(os.getloadavg())},
                'scope': '合成事件模型与未见种子有限诊断，不是网络性能或硬预算保证',
                'calibration_rule': '各修复方法同样六组合，跨场景/预算汇总实际违约数、超限、损失、调用、参数编码',
                'forecast_scope': '合法报告接收间隔/群与付费计数；隐藏包位置、延迟及费用仍为估计',
                'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}}
    for name in SOURCES:
        target = run / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    write_json(run / 'manifest.json', manifest)
    try:
        fees = calibration(ROOT)
        for key in ('enabled', 'threshold_us', 'read'):
            actual = fees['calls'][key]
            expected = MANAGEMENT.read_cost if key == 'read' else MANAGEMENT.cost_for_write(key)
            if (actual['request_bytes'], actual['reply_bytes']) != (expected.request_bytes, expected.reply_bytes):
                raise ValueError('API 费用不一致')
        write_json(run / 'rpc-calibration.json', fees)
        regression = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                                    cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (run / 'unit-tests.txt').write_text(regression.stdout)
        manifest['unit_regression'] = {'exit_code': regression.returncode, 'log': 'unit-tests.txt'}
        if regression.returncode:
            raise RuntimeError('单元检查失败')
        selections = []
        with (run / 'training.jsonl').open('w') as stream:
            for scenario in SCENARIOS:
                for level in ('low', 'high'):
                    selections.append(train([generate(scenario, seed) for seed in TRAIN_SEEDS], budget_for(scenario, level),
                                            Settings(), stream, scenario, level))
                print(json.dumps({'training_finished': scenario}), flush=True)
        write_json(run / 'training-selections.json', selections)

        def trained(s, level):
            return tuple(next(r['config'] for r in selections if r['scenario'] == s and r['budget_level'] == level))

        (run / 'calibration-workloads').mkdir()
        (run / 'calibration-runs').mkdir()
        for scenario in SCENARIOS:
            for seed in CALIBRATION_SEEDS:
                write_gzip(run / 'calibration-workloads' / f'{scenario}-{seed}.json.gz', generate(scenario, seed))
        tuning, frozen = [], {}
        with (run / 'calibration-summary.jsonl').open('w') as stream:
            for method in GUARDED:
                scores = []
                for index, parameters in enumerate(PARAMETERS):
                    rows = []
                    for scenario in SCENARIOS:
                        for level in ('low', 'high'):
                            for seed in CALIBRATION_SEEDS:
                                row, raw = adaptive_simulate(generate(scenario, seed), budget_for(scenario, level),
                                                             trained(scenario, level), method, parameters)
                                row.update(budget_level=level, parameter_index=index)
                                rows.append(row)
                                stream.write(json.dumps(plain(row), ensure_ascii=False, allow_nan=False) + '\n')
                                write_gzip(run / 'calibration-runs' / f'{scenario}-{level}-{seed}-{method}-p{index}.json.gz', raw)
                    score = calibration_score(rows, parameters)
                    scores.append((score, index))
                    tuning.append({'method': method, 'parameter_index': index, 'parameters': parameters, 'score': score, 'runs': len(rows)})
                    print(json.dumps({'calibration': method, 'parameter': index}), flush=True)
                _, best = min(scores)
                frozen[method] = best
        write_json(run / 'calibration-scores.json', tuning)
        write_json(run / 'frozen-parameters.json', {'selected_indices': frozen, 'parameters': PARAMETERS,
                                                  'frozen_utc': datetime.now(timezone.utc).isoformat(),
                                                  'calibration_seeds': CALIBRATION_SEEDS, 'test_not_generated': True})
        (run / 'workloads').mkdir()
        (run / 'runs').mkdir()
        summary = []
        for scenario in SCENARIOS:
            for seed in TEST_SEEDS:
                workload = generate(scenario, seed)
                write_gzip(run / 'workloads' / f'{scenario}-{seed}.json.gz', workload)
                for level in ('low', 'high'):
                    for method in METHODS:
                        parameters = PARAMETERS[frozen[method]] if method in GUARDED else AdaptiveSettings()
                        row, raw = adaptive_simulate(workload, budget_for(scenario, level), trained(scenario, level), method, parameters)
                        row['budget_level'] = level
                        summary.append(row)
                        write_gzip(run / 'runs' / f'{scenario}-{level}-{seed}-{method}.json.gz', raw)
            print(json.dumps({'heldout_finished': scenario}), flush=True)
        write_json(run / 'summary.json', summary)
        manifest.update(status='passed', heldout_runs=len(summary), training_runs=len(selections) * 256 * len(TRAIN_SEEDS),
                        calibration_runs=len(GUARDED) * len(PARAMETERS) * len(SCENARIOS) * 2 * len(CALIBRATION_SEEDS))
    except Exception as error:
        manifest.update(status='failed', error=str(error))
        (run / 'failure.txt').write_text(traceback.format_exc())
        raise
    finally:
        manifest['finished_utc'] = datetime.now(timezone.utc).isoformat()
        manifest['artifact_sha256'] = {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in sorted(run.rglob('*')) if p.is_file() and p.name != 'manifest.json'
                                       and 'executed-source' not in p.parts}
        write_json(run / 'manifest.json', manifest)
    print(json.dumps({k: manifest[k] for k in ('status', 'heldout_runs', 'training_runs', 'calibration_runs')}))


if __name__ == '__main__':
    main()
