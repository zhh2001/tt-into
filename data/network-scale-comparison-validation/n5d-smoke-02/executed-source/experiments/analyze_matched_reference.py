"""独立验收完整共同网格矩阵，保存全部方法、费用与配对差值。"""
import argparse
import csv
import hashlib
import itertools
import json
from pathlib import Path
import shutil
import statistics

from check_matched_reference import audit
from check_rolling_reference import load


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def utilization(row, budget, notice_cap):
    cost = row['cost']
    ratios = [cost['inband_per_link_bytes_per_s'][k] / v for k, v in budget['link_bytes_per_s'].items()]
    ratios.extend((cost['export_attempt_bytes_per_s'] / budget['export_bytes_per_s'],
                   sum(cost['control_serialized_bytes_by_kind'].values()) / 8 / budget['control_bytes_per_s'],
                   cost['local_notice_payload_bytes_per_s'] / notice_cap))
    return max(ratios)


def paired(rows, scenario, level, method, reference):
    def group(m):
        return {r['seed']: r for r in rows if (r['scenario'], r['budget_level'], r['method']) == (scenario, level, m)}
    a, b = group(method), group(reference)
    if a.keys() != b.keys() or len(a) != 4:
        raise ValueError('四种子配对不完整')
    seeds = sorted(a)
    diffs = [a[s]['loss'] - b[s]['loss'] for s in seeds]
    both = [s for s in seeds if a[s]['feasible'] and b[s]['feasible']]
    return {'scenario': scenario, 'budget_level': level, 'method': method, 'reference': reference,
            'seeds': seeds, 'loss_differences': diffs, 'mean_loss_difference': statistics.mean(diffs),
            'min_loss_difference': min(diffs), 'max_loss_difference': max(diffs),
            'both_feasible_seeds': both,
            'both_feasible_mean_loss_difference': None if not both else statistics.mean(a[s]['loss'] - b[s]['loss'] for s in both),
            'strict_improvement_on_all_four_with_both_feasible': len(both) == 4 and all(v < -1e-12 for v in diffs),
            'scope': '负值表示损失较低，四配对观察范围，未作显著性推断'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    # 验收不通过时保留输出目录与异常，不能生成 passed 清单。
    verification = audit(args.run, args.workers)
    write(args.output / 'checks.json', verification)
    m, rows = load(args.run / 'manifest.json'), load(args.run / 'summary.json')
    coverage_checks = 0
    for r in rows:
        s, b, seed, method = r['scenario'], r['budget_level'], r['seed'], r['method']
        w = load(args.run / 'workloads' / f'{s}-{seed}.json.gz')
        raw = load(args.run / 'runs' / f'{s}-{b}-{seed}-{method}.json.gz')
        covered = sum(t['weight'] for t in w['tasks'] if any(p['accepted'] and p['object_id'] == t['object_id']
                      and t['start_us'] <= p['received_us'] < t['end_us'] for p in raw['result']['receipts']))
        expected = covered / sum(t['weight'] for t in w['tasks'])
        if abs(r['coverage'] - expected) > 1e-12:
            raise ValueError('独立活跃任务区间覆盖分母核对失败')
        coverage_checks += 1
    verification['heldout_coverage_checks'] = coverage_checks
    write(args.output / 'checks.json', verification)
    aggregates, comparisons, failed = [], [], [r for r in rows if not r['feasible']]
    pairs = [(method, 'static') for method in m['methods'] if method != 'static']
    pairs += [('phase_guard', 'joint'), ('phase_guard', 'phase_local_guard')]
    for scenario, level in itertools.product(m['scenarios'], m['budgets']):
        for method in m['methods']:
            rs = [r for r in rows if (r['scenario'], r['budget_level'], r['method']) == (scenario, level, method)]
            if sorted(r['seed'] for r in rs) != sorted(m['test_seeds']):
                raise ValueError('完整汇总分母错误')
            losses = [r['loss'] for r in rs]
            utils = [utilization(r, m['budget_definition'][scenario][level], m['notice_budget_bytes_per_s']) for r in rs]
            entry = {'scenario': scenario, 'budget_level': level, 'method': method, 'seed_count': len(rs),
                     'mean_loss': statistics.mean(losses), 'min_loss': min(losses), 'max_loss': max(losses),
                     'mean_active_interval_coverage': statistics.mean(r['coverage'] for r in rs),
                     'total_window_feasible_seeds': sum(r['feasible'] for r in rs),
                     'mean_max_budget_utilization': statistics.mean(utils), 'max_budget_utilization': max(utils),
                     'mean_single_second_overruns': statistics.mean(r['over_budget_epoch_count'] for r in rs),
                     'mean_export_attempt_bytes_per_s': statistics.mean(r['cost']['export_attempt_bytes_per_s'] for r in rs),
                     'mean_api_serialized_bytes_per_s': statistics.mean(sum(r['cost']['control_serialized_bytes_by_kind'].values()) / 8 for r in rs),
                     'mean_local_notice_payload_bytes_per_s': statistics.mean(r['cost']['local_notice_payload_bytes_per_s'] for r in rs),
                     'mean_update_rpc_count': statistics.mean(r['update_rpc_count'] for r in rs),
                     'mean_decision_ms': statistics.mean(t / 1e6 for r in rs for t in r['decision_elapsed_ns']),
                     'max_decision_ms': max(t / 1e6 for r in rs for t in r['decision_elapsed_ns'])}
            for link in ('e1', 'e2', 'e3'):
                entry[f'mean_{link}_inband_bytes_per_s'] = statistics.mean(r['cost']['inband_per_link_bytes_per_s'][link] for r in rs)
            aggregates.append(entry)
        comparisons.extend(paired(rows, scenario, level, method, reference) for method, reference in pairs)
    with (args.output / 'aggregate.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(aggregates[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(aggregates)
    write(args.output / 'paired-comparisons.json', comparisons)
    write(args.output / 'over-budget-runs.json', failed)
    diagnostics = load(args.run / 'solver-diagnostics.json')
    solver = {method: {'exact_rank_matches': sum(d[method]['rank_matches'] for d in diagnostics),
                      'points': len(diagnostics), 'mean_objective_gap': statistics.mean(d[method]['objective_gap'] for d in diagnostics),
                      'max_objective_gap': max(d[method]['objective_gap'] for d in diagnostics)} for method in ('joint', 'local')}
    solver['scope'] = '固定校准视图的同代理网格搜索诊断，不是未来真实最优或保留样本'
    write(args.output / 'solver-summary.json', solver)
    findings = {'heldout_runs': len(rows), 'actual_feasible_runs': sum(r['feasible'] for r in rows),
                'feasible_runs_by_method': {method: sum(r['feasible'] for r in rows if r['method'] == method) for method in m['methods']},
                'four_pair_improvements_over_static': {method: [dict(scenario=c['scenario'], budget_level=c['budget_level'])
                    for c in comparisons if c['method'] == method and c['reference'] == 'static'
                    and c['strict_improvement_on_all_four_with_both_feasible']] for method in m['methods'] if method != 'static'},
                'selected_parameters': load(args.run / 'frozen-parameters.json'), 'solver': solver,
                'scope': '九场景、两预算、四保留种子的合成事件比较；完整配置集合与策略信息一致，无硬预算或网络性能结论',
                'single_second_scope': '单秒超限诊断独立于预声明总窗口平均达标，不将后者称逐秒监管'}
    write(args.output / 'findings.json', findings)
    sources = ('experiments/analyze_matched_reference.py', 'experiments/check_matched_reference.py',
               'experiments/check_adaptive_reference.py', 'experiments/check_rolling_reference.py', 'experiments/check_matched_preflight.py')
    root = Path(__file__).resolve().parents[1]
    for name in sources:
        target = args.output / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    record = {'status': 'passed', 'input_manifest_sha256': sha(args.run / 'manifest.json'),
              'source_sha256': {n: sha(root / n) for n in sources},
              'artifact_sha256': {p.name: sha(p) for p in args.output.iterdir() if p.is_file()},
              'scope': findings['scope']}
    write(args.output / 'manifest.json', record)
    print(json.dumps({'status': 'passed', 'checks': verification, 'feasible_runs': findings['actual_feasible_runs'],
                      'by_method': findings['feasible_runs_by_method']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
