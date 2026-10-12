"""全部配对、预测误差及事后密网格诊断分列，保留不利点。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys

from analyze_rolling_reference import control_rate, paired
from check_adaptive_reference import audit, audit_density
from check_rolling_reference import clipped_loss, load, violation


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--density', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verification = audit(args.run)
    density_verification = audit_density(args.density)
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    regression = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                                cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (args.output / 'unit-tests.txt').write_text(regression.stdout)
    if regression.returncode:
        raise RuntimeError('最终回归失败')
    write(args.output / 'checks.json', verification)
    write(args.output / 'density-checks.json', density_verification)
    manifest = load(args.run / 'manifest.json')
    rows, dense = load(args.run / 'summary.json'), load(args.density / 'summary.json')
    stats, comparisons, forecasts = [], [], []
    pairs = [('phase_guard', m) for m in ('static', 'joint', 'phase_joint', 'uniform_guard', 'phase_local_guard')]
    pairs += [(m, 'static') for m in ('joint', 'phase_joint', 'uniform_guard', 'phase_local_guard')]
    for scenario in manifest['scenarios']:
        for level in manifest['budgets']:
            for method in manifest['methods']:
                relevant = [r for r in rows if r['scenario'] == scenario and r['budget_level'] == level and r['method'] == method]
                stats.append({'scenario': scenario, 'budget_level': level, 'method': method, 'seeds': len(relevant),
                              'mean_loss': statistics.mean(r['loss'] for r in relevant),
                              'min_loss': min(r['loss'] for r in relevant), 'max_loss': max(r['loss'] for r in relevant),
                              'actual_feasible_seeds': sum(r['feasible'] for r in relevant),
                              'mean_export_bytes_per_s': statistics.mean(r['cost']['export_attempt_bytes_per_s'] for r in relevant),
                              'mean_control_bytes_per_s': statistics.mean(control_rate(r) for r in relevant),
                              'mean_over_budget_epochs': statistics.mean(r['over_budget_epoch_count'] for r in relevant),
                              'mean_update_rpcs': statistics.mean(r['update_rpc_count'] for r in relevant)})
            comparisons.extend(paired(rows, scenario, level, method, reference) for method, reference in pairs)
            for seed in manifest['test_seeds']:
                workload = load(args.run / 'workloads' / f'{scenario}-{seed}.json.gz')
                for method in manifest['methods']:
                    if method == 'static':
                        continue
                    raw = load(args.run / 'runs' / f'{scenario}-{level}-{seed}-{method}.json.gz')
                    for decision, cost in zip(raw['decisions'][2:], raw['epoch_costs']):
                        forecast = decision['selected']['forecast']
                        if forecast is None:
                            continue
                        left, right = cost['window_us']
                        tasks = [dict(t, start_us=max(t['start_us'], left), end_us=min(t['end_us'], right))
                                 for t in workload['tasks'] if max(t['start_us'], left) < min(t['end_us'], right)]
                        actual_generation = clipped_loss(tasks, raw['result']['receipts'], manifest['settings']['clip'])[0]
                        samples = [dict(r, generated_us=r['received_us']) for r in raw['result']['receipts'] if r['accepted']]
                        recency = clipped_loss(tasks, samples, manifest['settings']['clip'])[0]
                        nominal = manifest['budget_definition'][scenario][level]
                        guarded = forecast.get('guard_enabled', False)
                        effective = decision.get('budget_diagnostic', {}).get('effective_budget', nominal) if guarded else nominal
                        estimates = decision.get('budget_diagnostic', {}).get('spend')
                        true_past_export = sum(e['bytes'] for e in raw['result']['cost_events']
                                               if e['resource'] == 'export' and 2000000 <= e['time_us'] < left)
                        forecasts.append({'scenario': scenario, 'budget_level': level, 'seed': seed, 'method': method,
                                          'time_us': left, 'predicted_receipt_proxy_loss': forecast['predicted_loss'],
                                          'actual_generation_loss': actual_generation, 'actual_receipt_proxy_loss': recency,
                                          'receipt_loss_prediction_error': forecast['predicted_loss'] - recency,
                                          'predicted_export_bytes_per_s': forecast['cost']['export_attempt_bytes_per_s'],
                                          'actual_export_bytes_per_s': cost['export_attempt_bytes_per_s'],
                                          'predicted_nominal_epoch_excess': forecast['predicted_excess'],
                                          'actual_nominal_epoch_excess': violation(cost, nominal),
                                          'selection_budget_basis': '剩余窗口额度估计' if guarded else '名义周期预算',
                                          'predicted_effective_epoch_excess': forecast['effective_budget_excess'] if guarded else forecast['predicted_excess'],
                                          'actual_effective_epoch_excess': violation(cost, effective),
                                          'profile_modes': [p['mode'] for p in decision.get('profiles', ())],
                                          'estimated_past_export_bytes': None if estimates is None else estimates['export_bytes'],
                                          'actual_past_export_bytes': true_past_export})
    with (args.output / 'aggregate.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(stats[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(stats)
    write(args.output / 'paired-comparisons.json', comparisons)
    write(args.output / 'forecast-errors.json', forecasts)
    density_rows = []
    for scenario in ('stable', 'shared_priority'):
        d = [dict(r, method='dense_static') for r in dense if r['scenario'] == scenario]
        primary = [r for r in rows if r['scenario'] == scenario and r['budget_level'] == 'high']
        comparison = paired(primary + d, scenario, 'high', 'phase_guard', 'dense_static')
        density_rows.append({'scenario': scenario, 'scope': '事后挑战，配置空间不对称，种子已消费',
                             'dense_mean_loss': statistics.mean(r['loss'] for r in d),
                             'dense_mean_export_bytes_per_s': statistics.mean(r['cost']['export_attempt_bytes_per_s'] for r in d),
                             'dense_feasible_seeds': sum(r['feasible'] for r in d), 'comparison': comparison})
    write(args.output / 'density-diagnostic.json', density_rows)
    diagnostics = []
    for scenario in manifest['scenarios']:
        for method in ('joint', 'phase_joint', 'uniform_guard', 'phase_guard'):
            fs = [f for f in forecasts if f['scenario'] == scenario and f['method'] == method]
            diagnostics.append({'scenario': scenario, 'method': method,
                                'receipt_proxy_mean_absolute_error': statistics.mean(abs(f['receipt_loss_prediction_error']) for f in fs),
                                'predicted_feasible_actual_effective_overrun_epochs': sum(f['predicted_effective_epoch_excess'] <= 1e-12 and f['actual_effective_epoch_excess'] > 1e-12 for f in fs),
                                'past_export_estimate_mean_absolute_error_bytes': None if fs[0]['estimated_past_export_bytes'] is None else
                                    statistics.mean(abs(f['estimated_past_export_bytes'] - f['actual_past_export_bytes']) for f in fs)})
    findings = {'all_runs': len(rows), 'actual_feasible_runs': sum(r['feasible'] for r in rows),
                'actual_feasible_by_method': {m: sum(r['feasible'] for r in rows if r['method'] == m) for m in manifest['methods']},
                'primary_scope': '九场景、两预算、四个新种子的有限合成模型比较；不是网络性能结论',
                'scope_of_dense_challenge': '两个事后子集，未对动态方法使用相同密网格，不作一般优劣或最终论文结论',
                'forecast_diagnostics': diagnostics}
    write(args.output / 'findings.json', findings)
    sources = ('experiments/analyze_adaptive_reference.py', 'experiments/analyze_rolling_reference.py',
               'experiments/check_adaptive_reference.py', 'experiments/check_rolling_reference.py', 'tests/test_adaptive.py')
    for source in sources:
        target = args.output / 'executed-source' / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / source, target)
    record = {'status': 'passed', 'input_manifest_sha256': {kind: hashlib.sha256((path / 'manifest.json').read_bytes()).hexdigest()
                                                          for kind, path in (('primary', args.run), ('density', args.density))},
              'source_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources},
              'artifacts_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.iterdir() if p.is_file()},
              'unit_regression': {'exit_code': regression.returncode, 'log': 'unit-tests.txt'},
              'bootstrap': {'seed': 731, 'repeats': 2000}, 'scope': findings['primary_scope']}
    write(args.output / 'manifest.json', record)
    print(json.dumps({'status': 'passed', 'heldout_runs': len(rows), 'checks': verification, 'density_checks': density_verification}, ensure_ascii=False))


if __name__ == '__main__':
    main()
