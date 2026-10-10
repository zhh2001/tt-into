"""配对差值、实际预算前沿和预测误差诊断，不改变保留结果。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys

from check_rolling_reference import audit, clipped_loss, load, violation


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def interval(values, seed=731, repeats=2000):
    if not values:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(repeats))
    return {'mean': statistics.mean(values), 'percentile_95': [means[int(.025 * repeats)], means[int(.975 * repeats)]],
            'paired_seeds': len(values), 'bootstrap_repeats': repeats, 'bootstrap_seed': seed,
            'scope': '四种子有限诊断，非显著性证明；可行子集不能替代全部种子'}


def control_rate(row):
    start, end = row['cost']['window_us']
    return sum(row['cost']['control_serialized_bytes_by_kind'].values()) * 1e6 / (end - start)


def dominates(a, b):
    va = [a['loss'], a['cost']['export_attempt_bytes_per_s'], control_rate(a)] + list(a['cost']['inband_per_link_bytes_per_s'].values())
    vb = [b['loss'], b['cost']['export_attempt_bytes_per_s'], control_rate(b)] + list(b['cost']['inband_per_link_bytes_per_s'].values())
    return all(x <= y + 1e-10 for x, y in zip(va, vb)) and any(x < y - 1e-10 for x, y in zip(va, vb))


def paired(rows, scenario, level, method, reference):
    first = {r['seed']: r for r in rows if r['scenario'] == scenario and r['budget_level'] == level and r['method'] == method}
    second = {r['seed']: r for r in rows if r['scenario'] == scenario and r['budget_level'] == level and r['method'] == reference}
    values = []
    for seed in sorted(first):
        a, b = first[seed], second[seed]
        values.append({'seed': seed, 'loss_delta': a['loss'] - b['loss'],
                       'both_actual_feasible': a['feasible'] and b['feasible'],
                       'export_bytes_per_s_delta': a['cost']['export_attempt_bytes_per_s'] - b['cost']['export_attempt_bytes_per_s'],
                       'control_bytes_per_s_delta': control_rate(a) - control_rate(b),
                       'method_dominated_by_reference': dominates(b, a),
                       'reference_dominated_by_method': dominates(a, b)})
    return {'scenario': scenario, 'budget_level': level, 'method': method, 'reference': reference,
            'pairs': values, 'all_seed_diagnostic': interval([p['loss_delta'] for p in values]),
            'both_feasible_subset': interval([p['loss_delta'] for p in values if p['both_actual_feasible']]),
            'ranking_valid_for_all_seeds': all(p['both_actual_feasible'] for p in values)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verification = audit(args.run)
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    regression = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                                cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (args.output / 'unit-tests.txt').write_text(regression.stdout)
    if regression.returncode:
        raise RuntimeError('最终单元核对失败，日志保留')
    manifest = load(args.run / 'manifest.json')
    rows = load(args.run / 'summary.json')
    write(args.output / 'checks.json', verification)
    stats, comparisons, forecasts = [], [], []
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
            for method, reference in [('feedback', 'static'), ('local', 'static'), ('joint', 'static'), ('joint', 'local')]:
                comparisons.append(paired(rows, scenario, level, method, reference))
            for seed in manifest['test_seeds']:
                workload = load(args.run / 'workloads' / f'{scenario}-{seed}.json.gz')
                for method in ('feedback', 'local', 'joint'):
                    raw = load(args.run / 'runs' / f'{scenario}-{level}-{seed}-{method}.json.gz')
                    for decision, cost in zip(raw['decisions'][2:], raw['epoch_costs']):
                        forecast = decision['selected']['forecast']
                        if forecast is None:
                            continue
                        left, right = cost['window_us']
                        tasks = [dict(t, start_us=max(t['start_us'], left), end_us=min(t['end_us'], right))
                                 for t in workload['tasks'] if max(t['start_us'], left) < min(t['end_us'], right)]
                        actual_loss = clipped_loss(tasks, raw['result']['receipts'], manifest['settings']['clip'])[0]
                        receipts = [dict(r, generated_us=r['received_us']) for r in raw['result']['receipts'] if r['accepted']]
                        actual_recency = clipped_loss(tasks, receipts, manifest['settings']['clip'])[0]
                        forecasts.append({'scenario': scenario, 'budget_level': level, 'seed': seed, 'method': method,
                                          'time_us': left, 'predicted_receipt_proxy_loss': forecast['predicted_loss'],
                                          'actual_generation_loss': actual_loss, 'actual_receipt_proxy_loss': actual_recency,
                                          'receipt_loss_prediction_error': forecast['predicted_loss'] - actual_recency,
                                          'predicted_export_bytes_per_s': forecast['cost']['export_attempt_bytes_per_s'],
                                          'actual_export_bytes_per_s': cost['export_attempt_bytes_per_s'],
                                          'predicted_excess': forecast['predicted_excess'],
                                          'actual_excess': violation(cost, manifest['budget_definition'][scenario][level])})
    write(args.output / 'paired-comparisons.json', comparisons)
    write(args.output / 'forecast-errors.json', forecasts)
    with (args.output / 'aggregate.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(stats[0]))
        writer.writeheader()
        writer.writerows(stats)
    frontier = []
    for scenario in manifest['scenarios']:
        for seed in manifest['test_seeds']:
            points = [r for r in rows if r['scenario'] == scenario and r['seed'] == seed]
            for r in points:
                frontier.append({'scenario': scenario, 'seed': seed, 'budget_level': r['budget_level'], 'method': r['method'],
                                 'actual_feasible': r['feasible'],
                                 'dominated_by': [{'method': s['method'], 'budget_level': s['budget_level']}
                                                  for s in points if dominates(s, r)]})
    write(args.output / 'actual-cost-frontier.json', frontier)
    findings = {'all_runs': len(rows), 'actual_feasible_runs': sum(r['feasible'] for r in rows),
                'actual_feasible_by_method': {m: sum(r['feasible'] for r in rows if r['method'] == m) for m in manifest['methods']},
                'joint_vs_static': [{'scenario': c['scenario'], 'budget_level': c['budget_level'],
                                     'mean_loss_delta': c['all_seed_diagnostic']['mean'],
                                     'all_seed_actual_feasible': c['ranking_valid_for_all_seeds'],
                                     'mean_delta_both_feasible_subset': None if c['both_feasible_subset'] is None else c['both_feasible_subset']['mean']}
                                    for c in comparisons if c['method'] == 'joint' and c['reference'] == 'static'],
                'joint_forecast_by_scenario': [
                    {'scenario': s,
                     'receipt_proxy_mean_absolute_error': statistics.mean(abs(f['receipt_loss_prediction_error']) for f in forecasts
                                                                          if f['scenario'] == s and f['method'] == 'joint'),
                     'predicted_feasible_actual_overrun_epochs': sum(f['predicted_excess'] <= 1e-12 and f['actual_excess'] > 1e-12
                                                                     for f in forecasts if f['scenario'] == s and f['method'] == 'joint')}
                    for s in manifest['scenarios']],
                'scope': '合成事件模型的全部保留结果。配对区间仅作诊断，不对超预算点声称同预算收益。'}
    write(args.output / 'findings.json', findings)
    sources = ('experiments/check_rolling_reference.py', 'experiments/analyze_rolling_reference.py', 'tests/test_rolling.py')
    for name in sources:
        target = args.output / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    record = {'status': 'passed', 'input_manifest_sha256': hashlib.sha256((args.run / 'manifest.json').read_bytes()).hexdigest(),
              'input_summary_sha256': hashlib.sha256((args.run / 'summary.json').read_bytes()).hexdigest(),
              'source_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources},
              'artifacts_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(args.output.iterdir()) if p.is_file()},
              'bootstrap': {'repeats': 2000, 'seed': 731, 'paired_within_seed': True},
              'unit_regression': {'exit_code': regression.returncode, 'log': 'unit-tests.txt',
                                  'command': 'python3 -m unittest discover -s tests -v'},
              'scope': findings['scope']}
    write(args.output / 'manifest.json', record)
    print(json.dumps(findings, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
