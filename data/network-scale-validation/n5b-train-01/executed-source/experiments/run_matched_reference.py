"""共同网格的完整事件训练、独立校准和新保留比较。"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
import gzip
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
from orchestration.accounted_engine import AccountedEngine, layered_costs, layered_excess, managed_notice
from orchestration.adaptive import AdaptiveSettings, history_entry, select
from orchestration.event_metrics import evaluate_tasks
from orchestration.event_schema import Read, Write
from orchestration.matched_grid import ControlCaps, METHODS, MatchedProxy, settings
from orchestration.rolling import changes, choose, config_code, configurations, installation, loss
from adaptive_workloads import SCENARIOS, generate
from rolling_workloads import MEASUREMENT, NODES, budget_for
from run_matched_preflight import SOURCES as PRE_SOURCES, decode_view, matched_management, measured_fees

TRAIN_SEEDS = (11, 29)
CALIBRATION_SEEDS = (1301, 1423)
TEST_SEEDS = (1601, 1723, 1871, 1999)
GUARDED = ('uniform_guard', 'phase_guard', 'phase_local_guard')
PARAMETERS = tuple(AdaptiveSettings(gain_margin=g, reserve_fraction=r) for g, r in itertools.product((0.0, .05, .15), (0.0, .1)))
SOURCES = tuple(dict.fromkeys(PRE_SOURCES + ('src/orchestration/accounted_engine.py', 'experiments/adaptive_workloads.py',
    'experiments/run_matched_reference.py', 'experiments/check_matched_reference.py',
    'experiments/check_rolling_reference.py', 'experiments/check_adaptive_reference.py', 'tests/test_accounted_engine.py')))


def plain(v):
    if is_dataclass(v):
        return plain(asdict(v))
    if isinstance(v, bytes):
        return v.hex()
    if isinstance(v, dict):
        return {str(k): plain(value) for k, value in v.items()}
    if isinstance(v, (tuple, list)):
        return [plain(value) for value in v]
    return v


def write_json(path, value):
    path.write_text(json.dumps(plain(value), ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def write_gzip(path, value):
    with path.open('wb') as f, gzip.GzipFile(fileobj=f, mode='wb', mtime=0) as stream:
        stream.write((json.dumps(plain(value), ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def workload(scenario, seed):
    w = generate(scenario, seed)
    return replace(w, notices=tuple(managed_notice(n, settings().epoch_us) for n in w.notices))


def caps_for(budget):
    return ControlCaps(budget.control_bytes_per_s, 128)


def simulate(w, budget, trained, method, parameters, management):
    opts, caps = settings(), caps_for(budget)
    engine = AccountedEngine(NODES, management.routes, w.packets, installation(trained, management, opts),
                             capacity=opts.capacity, notices=w.notices)
    decisions, history = [], []
    temporal = method in ('phase_joint', 'phase_guard', 'phase_local_guard')
    guarded = method in GUARDED
    for epoch in range(10):
        now = epoch * opts.epoch_us
        view = engine.run_until(now)
        control = engine.control_snapshot()
        p = MatchedProxy(view, management, budget, opts, parameters, control, caps, tuple(history), temporal, guarded)
        started = time.perf_counter_ns()
        chosen = select(p, trained, method == 'phase_local_guard') if method not in METHODS[:4] else choose(p, method, trained)
        elapsed = time.perf_counter_ns() - started
        diffs = changes(chosen['config'], p.fields, management)
        if method == 'static' and diffs or len(diffs) > opts.max_update_rpcs:
            raise ValueError('静态配置改变或更新限额超出')
        writes, reads = [], []
        for i, ((node, flow), field, value) in enumerate(diffs):
            issued = now + 1 + i * 200
            op = Write(f'{method}-{epoch}-{i}', node, flow, field, value, epoch + 2,
                       issued, issued + 20 + node * 10, issued + 100, management.cost_for_write(field))
            engine.submit_write(op)
            writes.append(op)
        if method != 'static':
            for i, (flow, route) in enumerate(sorted(management.routes.items())):
                issued = now + 1 + i * 10
                op = Read(f'{method}-{epoch}-count-{i}', flow, route[0].node, issued,
                          issued + 50, issued + 100, management.read_cost)
                engine.submit_read(op)
                reads.append(op)
        decisions.append({'time_us': now, 'view': view, 'control_snapshot': control, 'current_config': p.current,
            'selected': chosen, 'writes': writes, 'reads': reads, 'decision_elapsed_ns': elapsed,
            'rates': [{'flow': f, 'rate_pps': p.rates.get(f)} for f in sorted(management.routes)], 'profiles': p.profiles,
            'budget_history': tuple(history), 'budget_diagnostic': p.budget_diagnostic, 'candidate_scores': list(p.memo.values())})
        h = history_entry(p, chosen)
        if h is not None:
            history.append(h)
    result = engine.run()
    metrics = evaluate_tasks(w.tasks, result['receipts'], opts.clip)
    cost = layered_costs(result['cost_events'], *MEASUREMENT, budget.link_bytes_per_s, budget.export_bytes_per_s)
    epochs = [layered_costs(result['cost_events'], i * opts.epoch_us, (i + 1) * opts.epoch_us) for i in range(2, 10)]
    over = layered_excess(cost, budget, caps)
    row = {'scenario': w.scenario, 'seed': w.seed, 'method': method, 'loss': metrics['weighted_clipped_normalized_loss'],
           'coverage': metrics['weighted_active_interval_coverage'], 'cost': cost, 'excess': over, 'feasible': over <= 1e-12,
           'over_budget_epoch_count': sum(layered_excess(c, budget, caps) > 1e-12 for c in epochs),
           'update_rpc_count': sum(len(d['writes']) for d in decisions[2:]),
           'decision_elapsed_ns': [d['decision_elapsed_ns'] for d in decisions]}
    return row, {'summary': row, 'decisions': decisions, 'result': result, 'task_metrics': metrics, 'epoch_costs': epochs}


def training_job(scenario, seed, out, fees):
    management, opts = matched_management(fees), settings()
    w = workload(scenario, seed)
    started = time.perf_counter()
    path = Path(out) / 'training' / f'{scenario}-{seed}.jsonl.gz'
    with path.open('wb') as f, gzip.GzipFile(fileobj=f, mode='wb', mtime=0) as zipped:
        for config in configurations(management, opts):
            engine = AccountedEngine(NODES, management.routes, w.packets, installation(config, management, opts),
                                     capacity=opts.capacity, notices=w.notices)
            r = engine.run()
            cost = layered_costs(r['cost_events'], *MEASUREMENT)
            base = {'scenario': scenario, 'seed': seed, 'config': config, 'loss': loss(w.tasks, r['receipts'], opts.clip), 'cost': cost}
            base['budget_scores'] = {level: layered_excess(cost, budget_for(scenario, level), caps_for(budget_for(scenario, level)))
                                     for level in ('low', 'high')}
            zipped.write((json.dumps(plain(base), ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode())
    return {'scenario': scenario, 'seed': seed, 'static_replays': 6561, 'budget_scores': 13122, 'elapsed_s': time.perf_counter() - started}


def training_selections(run):
    selections = []
    for scenario in SCENARIOS:
        groups = []
        for seed in TRAIN_SEEDS:
            with gzip.open(run / 'training' / f'{scenario}-{seed}.jsonl.gz', 'rt') as stream:
                groups.append([json.loads(line) for line in stream])
        for level in ('low', 'high'):
            scored = []
            for rows in zip(*groups):
                if rows[0]['config'] != rows[1]['config']:
                    raise ValueError('静态训练输入配置未对齐')
                c = tuple(rows[0]['config'])
                score = (max(r['budget_scores'][level] for r in rows), sum(r['loss'] for r in rows) / len(rows),
                         sum(v is not None for v in c), config_code(c))
                scored.append((score, c))
            score, config = min(scored)
            selections.append({'scenario': scenario, 'budget_level': level, 'config': config, 'score': score,
                               'training_seeds': TRAIN_SEEDS, 'configuration_count': len(scored)})
    return selections


def calibration_score(rows, p):
    return (sum(not r['feasible'] for r in rows), sum(r['excess'] for r in rows), sum(r['loss'] for r in rows) / len(rows),
            sum(r['update_rpc_count'] for r in rows), p.gain_margin, p.reserve_fraction)


def solver_diagnostics(run, management, selections, frozen):
    diagnostics = []
    for scenario, level, at in itertools.product(('stable', 'shared_priority', 'unannounced_switch'), ('low', 'high'), (4000000, 7000000)):
        index = frozen['phase_guard']
        path = run / 'calibration-runs' / f'{scenario}-{level}-{CALIBRATION_SEEDS[0]}-phase_guard-p{index}.json.gz'
        with gzip.open(path, 'rt') as stream:
            raw = json.load(stream)
        d = next(d for d in raw['decisions'] if d['time_us'] == at)
        from orchestration.control_ledger import Charge, ControlSnapshot
        control = ControlSnapshot(d['control_snapshot']['cutoff_ns'], tuple(Charge(**r) for r in d['control_snapshot']['charges']))
        trained = tuple(next(s['config'] for s in selections if s['scenario'] == scenario and s['budget_level'] == level))
        def make():
            return MatchedProxy(decode_view(d['view']), management, budget_for(scenario, level), settings(), PARAMETERS[index],
                                control, caps_for(budget_for(scenario, level)), tuple(d['budget_history']))
        exact = make()
        candidates = [exact.evaluate(c) for c in configurations(management, settings())]
        eligible = [r for r in candidates if r['update_allowed']]
        entry = {'scenario': scenario, 'budget_level': level, 'time_us': at, 'parameter_index': index,
                 'input_calibration_run': str(path.relative_to(run)), 'configuration_count': len(candidates),
                 'scope': '合法预测代理网格参照，非真实未来最优'}
        for method, local in (('joint', False), ('local', True)):
            best = min(eligible, key=lambda r: exact.rank(r, local))
            limited = choose(make(), method, trained)
            row = limited['forecast']
            entry[method] = {'exact': best, 'limited': limited, 'rank_matches': exact.rank(best, local) == exact.rank(row, local),
                            'objective_gap': row['local_loss' if local else 'predicted_loss'] - best['local_loss' if local else 'predicted_loss']}
        write_gzip(run / 'solver' / f'{scenario}-{level}-{at}.json.gz', {'diagnostic': entry, 'candidates': candidates})
        diagnostics.append(entry)
    write_json(run / 'solver-diagnostics.json', diagnostics)


def seed_audit():
    used, files = set(), []
    def visit(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if not k.startswith('reserved_') and (k in ('seed', 'seeds', 'random_seed') or k.endswith('_seeds')):
                    items = x if isinstance(x, list) else [x]
                    used.update(a for a in items if type(a) is int)
                visit(x)
        elif isinstance(v, list):
            for x in v:
                visit(x)
    for p in (ROOT / 'data').rglob('manifest.json'):
        record = json.loads(p.read_text())
        # 本轮开始前，纯预留清单不是实际生成输入。
        visit(record)
        files.append({'path': str(p.relative_to(ROOT)), 'sha256': sha(p)})
    conflicts = used & set(CALIBRATION_SEEDS + TEST_SEEDS)
    if conflicts:
        raise ValueError('新分组已消费：' + str(sorted(conflicts)))
    existing = [str(p.relative_to(ROOT)) for p in (ROOT / 'data').rglob('*.json.gz')
                if any(f'-{seed}.' in p.name or f'-{seed}-' in p.name for seed in CALIBRATION_SEEDS + TEST_SEEDS)]
    if existing:
        raise ValueError('新种子对应的业务或回放文件已存在')
    return {'historical_manifests': files, 'used_seeds': sorted(used), 'new_seeds_generated': False,
            'scope': '预留字段排除，另查实际压缩业务与回放文件名；分组冻结前执行'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error('worker 数须为 1..16')
    history_audit = seed_audit()
    run = args.output
    run.mkdir(parents=True, exist_ok=False)
    n3 = ROOT / 'data/into-validation/n3-validation-01'
    fees = measured_fees(n3)
    management = matched_management(fees)
    manifest = {'experiment_id': run.name, 'status': 'running', 'started_utc': datetime.now(timezone.utc).isoformat(),
        'kind': 'matched_grid_accounted_event_model', 'settings': settings(), 'methods': METHODS, 'scenarios': SCENARIOS,
        'budgets': ['low', 'high'], 'train_seeds': TRAIN_SEEDS, 'calibration_seeds': CALIBRATION_SEEDS, 'test_seeds': TEST_SEEDS,
        'adaptive_parameters': PARAMETERS, 'measurement_window_us': MEASUREMENT, 'workers': args.workers,
        'topology': {'nodes': NODES, 'routes': [{'flow': f, 'objects': r} for f, r in management.routes.items()]},
        'budget_definition': {s: {b: budget_for(s, b) for b in ('low', 'high')} for s in SCENARIOS},
        'notice_budget_bytes_per_s': 128, 'n3_manifest_sha256': sha(n3 / 'manifest.json'),
        'environment': {'python': platform.python_version(), 'cpu_count': os.cpu_count(), 'load_start': list(os.getloadavg())},
        'scope': '共同网格合成事件执行，不是 BMv2 网络性能、真实队列或硬预算保证',
        'notice_contract': '需求释放后 1 us 合成到达，再按整数秒轮询可用；全方法同额有效负载，真实任务区间不后移',
        'control_time_contract': '沿用共同合成写/读先验，非 N3 时延实测',
        'source_sha256': {n: sha(ROOT / n) for n in SOURCES}}
    write_json(run / 'manifest.json', manifest)
    write_json(run / 'seed-audit.json', history_audit)
    write_json(run / 'rpc-reference.json', fees)
    for n in SOURCES:
        p = run / 'executed-source' / n
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / n, p)
    for name in ('training', 'training-workloads', 'calibration-runs', 'calibration-workloads', 'solver', 'runs', 'workloads'):
        (run / name).mkdir()
    try:
        regression = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=ROOT,
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (run / 'unit-tests.txt').write_text(regression.stdout)
        manifest['unit_exit_code'] = regression.returncode
        if regression.returncode:
            raise ValueError('回归失败')
        for s, seed in itertools.product(SCENARIOS, TRAIN_SEEDS):
            write_gzip(run / 'training-workloads' / f'{s}-{seed}.json.gz', workload(s, seed))
        jobs = []
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(training_job, s, seed, str(run), fees) for s, seed in itertools.product(SCENARIOS, TRAIN_SEEDS)]
            for f in as_completed(futures):
                row = f.result()
                jobs.append(row)
                print(json.dumps({'training_finished': row['scenario'], 'seed': row['seed']}), flush=True)
        write_json(run / 'training-jobs.json', sorted(jobs, key=lambda r: (r['scenario'], r['seed'])))
        selections = training_selections(run)
        write_json(run / 'training-selections.json', selections)
        def trained(s, b):
            return tuple(next(r['config'] for r in selections if r['scenario'] == s and r['budget_level'] == b))
        for s, seed in itertools.product(SCENARIOS, CALIBRATION_SEEDS):
            write_gzip(run / 'calibration-workloads' / f'{s}-{seed}.json.gz', workload(s, seed))
        tuning, frozen = [], {}
        with (run / 'calibration-summary.jsonl').open('w') as stream:
            for method in GUARDED:
                scored = []
                for i, p in enumerate(PARAMETERS):
                    rows = []
                    for s, b, seed in itertools.product(SCENARIOS, ('low', 'high'), CALIBRATION_SEEDS):
                        row, raw = simulate(workload(s, seed), budget_for(s, b), trained(s, b), method, p, management)
                        row.update(budget_level=b, parameter_index=i)
                        rows.append(row)
                        stream.write(json.dumps(plain(row), ensure_ascii=False, allow_nan=False) + '\n')
                        write_gzip(run / 'calibration-runs' / f'{s}-{b}-{seed}-{method}-p{i}.json.gz', raw)
                    score = calibration_score(rows, p)
                    scored.append((score, i))
                    tuning.append({'method': method, 'parameter_index': i, 'parameters': p, 'score': score, 'runs': len(rows)})
                    print(json.dumps({'calibration_finished': method, 'parameter': i}), flush=True)
                frozen[method] = min(scored)[1]
        write_json(run / 'calibration-scores.json', tuning)
        write_json(run / 'frozen-parameters.json', {'selected_indices': frozen, 'parameters': PARAMETERS,
            'frozen_utc': datetime.now(timezone.utc).isoformat(), 'test_not_generated': True,
            'training_selection_sha256': sha(run / 'training-selections.json')})
        solver_diagnostics(run, management, selections, frozen)
        heldout = []
        for s, seed in itertools.product(SCENARIOS, TEST_SEEDS):
            w = workload(s, seed)
            write_gzip(run / 'workloads' / f'{s}-{seed}.json.gz', w)
            for b, method in itertools.product(('low', 'high'), METHODS):
                p = PARAMETERS[frozen[method]] if method in GUARDED else AdaptiveSettings()
                row, raw = simulate(w, budget_for(s, b), trained(s, b), method, p, management)
                row['budget_level'] = b
                heldout.append(row)
                write_gzip(run / 'runs' / f'{s}-{b}-{seed}-{method}.json.gz', raw)
            if seed == TEST_SEEDS[-1]:
                print(json.dumps({'heldout_finished': s}), flush=True)
        write_json(run / 'summary.json', heldout)
        manifest.update(status='passed', static_replays=118098, static_budget_scores=236196, calibration_runs=648,
                        heldout_runs=len(heldout), solver_diagnostic_points=12)
    except Exception as exc:
        manifest.update(status='failed', error=str(exc))
        (run / 'failure.txt').write_text(traceback.format_exc())
        raise
    finally:
        manifest.update(finished_utc=datetime.now(timezone.utc).isoformat())
        manifest['environment']['load_end'] = list(os.getloadavg())
        manifest['artifact_sha256'] = {str(p.relative_to(run)): sha(p) for p in run.rglob('*') if p.is_file()
            and p.name != 'manifest.json' and 'executed-source' not in p.parts}
        write_json(run / 'manifest.json', manifest)
    print(json.dumps({k: manifest[k] for k in ('status', 'static_replays', 'static_budget_scores', 'calibration_runs', 'heldout_runs')}))


if __name__ == '__main__':
    main()
