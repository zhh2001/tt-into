"""训练静态配置并执行隔离的保留种子闭环模型。"""
import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import gzip
import hashlib
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
from orchestration.event_engine import EventEngine
from orchestration.event_metrics import evaluate_tasks, window_costs
from orchestration.event_schema import Read, Write
from orchestration.rolling import (OpportunityProxy, Settings, changes, choose, config_code, configurations,
                                   exact_proxy, excess, installation, loss)
from rolling_rpc_cost import calibration, costs
from rolling_workloads import (MANAGEMENT, MEASUREMENT, NODES, SCENARIOS, TEST_SEEDS, TRAIN_SEEDS,
                               budget_for, generate)

METHODS = ('static', 'feedback', 'local', 'joint')
SOURCES = ('src/collector/tagged_codec.py', 'src/orchestration/event_schema.py',
           'src/orchestration/event_engine.py', 'src/orchestration/event_metrics.py',
           'src/orchestration/event_observation.py', 'src/orchestration/metrics.py',
           'src/orchestration/rolling.py', 'src/control/bmv2.py',
           'experiments/rolling_workloads.py', 'experiments/rolling_rpc_cost.py',
           'experiments/run_rolling_reference.py', 'tests/test_rolling.py')


def plain(value):
    if is_dataclass(value):
        return {k: plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value


def write_json(path, value):
    path.write_text(json.dumps(plain(value), ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def write_gzip(path, value):
    # 固定 gzip 头时间，结果文件内的运算时间仍保留实际值。
    with path.open('wb') as stream, gzip.GzipFile(fileobj=stream, mode='wb', mtime=0) as compressed:
        compressed.write((json.dumps(plain(value), ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode())


def static_replay(workload, config, settings):
    engine = EventEngine(NODES, MANAGEMENT.routes, workload.packets,
                         installation(config, MANAGEMENT, settings), capacity=settings.capacity, notices=workload.notices)
    result = engine.run()
    return result, window_costs(result['cost_events'], *MEASUREMENT)


def train(workloads, budget, settings, stream, scenario, level):
    scores = []
    for config in configurations(MANAGEMENT, settings):
        seed_rows = []
        for workload in workloads:
            result, cost = static_replay(workload, config, settings)
            row = {'scenario': scenario, 'budget_level': level, 'seed': workload.seed, 'config': config,
                   'loss': loss(workload.tasks, result['receipts'], settings.clip),
                   'cost': cost, 'excess': excess(cost, budget)}
            stream.write(json.dumps(plain(row), ensure_ascii=False, allow_nan=False) + '\n')
            seed_rows.append(row)
        score = (max(r['excess'] for r in seed_rows), sum(r['loss'] for r in seed_rows) / len(seed_rows),
                 sum(v is not None for v in config), config_code(config))
        scores.append((score, config))
    score, config = min(scores)
    return {'scenario': scenario, 'budget_level': level, 'config': config, 'score': score,
            'training_seeds': tuple(w.seed for w in workloads), 'configuration_count': len(scores)}


def simulate(workload, budget, trained, method, settings, exact_diagnostic=False):
    """实际引擎只交出 view。选择函数不接收工作负载或结果真值。"""
    engine = EventEngine(NODES, MANAGEMENT.routes, workload.packets,
                         installation(trained, MANAGEMENT, settings), capacity=settings.capacity, notices=workload.notices)
    decisions, diagnostics = [], []
    for epoch in range(10):
        now = epoch * settings.epoch_us
        view = engine.run_until(now)
        proxy = OpportunityProxy(view, MANAGEMENT, budget, settings)
        started = time.perf_counter_ns()
        selected = choose(proxy, method, trained)
        elapsed_ns = time.perf_counter_ns() - started
        diffs = changes(selected['config'], proxy.fields, MANAGEMENT)
        if method == 'static' and diffs:
            raise ValueError('静态初始安装未确认或配置发生变化')
        if len(diffs) > settings.max_update_rpcs:
            raise ValueError('策略输出违反每周期更新限额')
        issued = []
        for ordinal, (key, field, value) in enumerate(diffs):
            node, flow = key
            issued_at = now + 1 + ordinal * 200
            write = Write(f'{method}-{epoch}-{ordinal}', node, flow, field, value, epoch + 2,
                          issued_at, issued_at + 20 + node * 10, issued_at + 100,
                          MANAGEMENT.cost_for_write(field))
            engine.submit_write(write)
            issued.append(write)
        reads = []
        if method != 'static':
            for ordinal, (flow, route) in enumerate(sorted(MANAGEMENT.routes.items())):
                issued_at = now + 1 + ordinal * 10
                read = Read(f'{method}-{epoch}-count-{ordinal}', flow, route[0].node,
                            issued_at, issued_at + 50, issued_at + 100, MANAGEMENT.read_cost)
                engine.submit_read(read)
                reads.append(read)
        decisions.append({'time_us': now, 'view': view, 'rates': [{'flow': f, 'rate_pps': proxy.rates.get(f)}
                                                                 for f in sorted(MANAGEMENT.routes)],
                          'current_config': proxy.current, 'selected': selected, 'writes': issued, 'reads': reads,
                          'decision_elapsed_ns': elapsed_ns,
                          'candidate_scores': list(proxy.memo.values()),
                          'predicted_loss_basis': '已收新近度播种的均匀机会代理，不是生成年龄真值'})
        if exact_diagnostic and epoch == 2 and method == 'joint':
            exact = exact_proxy(OpportunityProxy(view, MANAGEMENT, budget, settings))
            diagnostics.append({'time_us': now, 'kind': 'finite_proxy_enumeration',
                                'searched': selected['forecast'], 'exact': exact,
                                'loss_gap': selected['forecast']['predicted_loss'] - exact['predicted_loss'],
                                'scope': '同一合法预测代理的网格差距，不是实际未来最优'})
    result = engine.run()
    metrics = evaluate_tasks(workload.tasks, result['receipts'], settings.clip)
    cost = window_costs(result['cost_events'], *MEASUREMENT, budget.link_bytes_per_s, budget.export_bytes_per_s)
    epoch_costs = [window_costs(result['cost_events'], t * settings.epoch_us, (t + 1) * settings.epoch_us)
                   for t in range(2, 10)]
    row = {'scenario': workload.scenario, 'seed': workload.seed, 'method': method,
           'loss': metrics['weighted_clipped_normalized_loss'],
           'coverage': metrics['weighted_active_interval_coverage'],
           'cost': cost, 'excess': excess(cost, budget), 'feasible': excess(cost, budget) <= 1e-12,
           'over_budget_epoch_count': sum(excess(c, budget) > 1e-12 for c in epoch_costs),
           'update_rpc_count': sum(len(d['writes']) for d in decisions if MEASUREMENT[0] <= d['time_us'] < MEASUREMENT[1]),
           'decision_elapsed_ns': [d['decision_elapsed_ns'] for d in decisions]}
    return row, {'summary': row, 'decisions': decisions, 'result': result, 'task_metrics': metrics,
                 'epoch_costs': epoch_costs, 'diagnostics': diagnostics}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run = args.output
    run.mkdir(parents=True, exist_ok=False)
    settings = Settings()
    manifest = {'experiment_id': run.name, 'status': 'running', 'started_utc': datetime.now(timezone.utc).isoformat(),
                'kind': 'causal_rolling_event_model', 'settings': plain(settings), 'methods': METHODS,
                'scenarios': SCENARIOS, 'budgets': ['low', 'high'], 'train_seeds': TRAIN_SEEDS, 'test_seeds': TEST_SEEDS,
                'measurement_window_us': MEASUREMENT, 'topology': {'nodes': NODES, 'routes':
                    [{'flow': f, 'objects': route} for f, route in MANAGEMENT.routes.items()]},
                'budget_definition': {scenario: {level: budget_for(scenario, level) for level in ('low', 'high')}
                                      for scenario in SCENARIOS},
                'environment': {'python': platform.python_version(), 'cpu_count': os.cpu_count(),
                                'load_average_at_start': list(os.getloadavg())},
                'scope': '合成事件模型；无 BMv2 闭环或真实排队服务；四种子有限诊断',
                'forecast_scope': '计数 EWMA 均匀机会、新近度与估计门控；不读取未来到包或损伤标签',
                'training_selection': '最大训练预算超限、平均时效损失、启用键数、配置编码的字典序',
                'solver_diagnostic_subset': 'stable/shared_priority，高预算，seed=101，joint在2s的代理网格',
                'offline_static_oracle_subset': 'stable/shared_priority，高预算，seed=101，256配置，仅事后诊断',
                'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}}
    for name in SOURCES:
        target = run / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    write_json(run / 'manifest.json', manifest)
    try:
        fees = calibration(ROOT)
        measured = costs(fees)
        for field in ('enabled', 'threshold_us'):
            actual, expected = measured[field], MANAGEMENT.cost_for_write(field)
            if (actual.request_bytes, actual.reply_bytes) != (expected.request_bytes, expected.reply_bytes):
                raise ValueError('运行费用与当前绑定不一致')
        if (measured['read'].request_bytes, measured['read'].reply_bytes) != (MANAGEMENT.read_cost.request_bytes, MANAGEMENT.read_cost.reply_bytes):
            raise ValueError('计数读取费用不一致')
        write_json(run / 'rpc-calibration.json', fees)
        command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']
        check = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        (run / 'unit-tests.txt').write_text(check.stdout)
        manifest['unit_regression'] = {'exit_code': check.returncode, 'log': 'unit-tests.txt'}
        if check.returncode:
            raise RuntimeError('单元回归失败')
        selections = []
        with (run / 'training.jsonl').open('w') as stream:
            for scenario in SCENARIOS:
                workloads = [generate(scenario, seed) for seed in TRAIN_SEEDS]
                for level in ('low', 'high'):
                    selection = train(workloads, budget_for(scenario, level), settings, stream, scenario, level)
                    selections.append(selection)
                print(json.dumps({'training_finished': scenario}), flush=True)
        write_json(run / 'training-selections.json', selections)
        (run / 'workloads').mkdir()
        (run / 'runs').mkdir()
        summary, oracles = [], []
        for scenario in SCENARIOS:
            for seed in TEST_SEEDS:
                workload = generate(scenario, seed)
                write_gzip(run / 'workloads' / f'{scenario}-{seed}.json.gz', workload)
                for level in ('low', 'high'):
                    budget = budget_for(scenario, level)
                    trained = tuple(next(r['config'] for r in selections if r['scenario'] == scenario and r['budget_level'] == level))
                    diagnostic = scenario in ('stable', 'shared_priority') and level == 'high' and seed == TEST_SEEDS[0]
                    for method in METHODS:
                        row, raw = simulate(workload, budget, trained, method, settings, diagnostic)
                        row['budget_level'] = level
                        summary.append(row)
                        write_gzip(run / 'runs' / f'{scenario}-{level}-{seed}-{method}.json.gz', raw)
                    if diagnostic:
                        options = []
                        for config in configurations(MANAGEMENT, settings):
                            result, cost = static_replay(workload, config, settings)
                            options.append({'config': config, 'loss': loss(workload.tasks, result['receipts'], settings.clip),
                                            'cost': cost, 'excess': excess(cost, budget)})
                        feasible = [o for o in options if o['excess'] <= 1e-12]
                        oracles.append({'scenario': scenario, 'seed': seed, 'budget_level': level,
                                        'scope': '事后未来已知静态枚举，不是在线方法或动态最优',
                                        'all_configurations': options,
                                        'best_feasible': min(feasible, key=lambda o: (o['loss'], config_code(o['config']))) if feasible else None})
            print(json.dumps({'heldout_finished': scenario}), flush=True)
        write_json(run / 'summary.json', summary)
        write_json(run / 'offline-static-oracles.json', oracles)
        manifest.update(status='passed', heldout_runs=len(summary), training_runs=len(selections) * 256 * len(TRAIN_SEEDS),
                        finished_utc=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        manifest.update(status='failed', error=str(error), finished_utc=datetime.now(timezone.utc).isoformat())
        (run / 'failure.txt').write_text(traceback.format_exc())
        raise
    finally:
        manifest['artifact_sha256'] = {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in sorted(run.rglob('*')) if p.is_file() and p.name != 'manifest.json'
                                       and 'executed-source' not in p.parts}
        write_json(run / 'manifest.json', manifest)
    print(json.dumps({'status': manifest['status'], 'heldout_runs': len(summary), 'training_runs': manifest['training_runs']}))


if __name__ == '__main__':
    main()
