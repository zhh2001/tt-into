"""完整比较的离线观察链、任务损失分解与两部署首次分歧诊断。"""
import argparse
from collections import Counter, defaultdict
from pathlib import Path
import shutil
import statistics
import time

from observation_diagnostics import (INPUT_SHA256, bind_input, case_diagnostics, load,
                                     near, rows, sha, table, write)

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('experiments/observation_diagnostics.py', 'experiments/diagnose_network_observation.py',
           'experiments/check_network_observation.py', 'tests/test_network_observation.py')


def difference(dynamic, static, prefix):
    lo = dynamic[prefix + '_loss_lower'] - static[prefix + '_loss_upper']
    hi = dynamic[prefix + '_loss_upper'] - static[prefix + '_loss_lower']
    return {'lower': lo, 'upper': hi, 'direction': 'lower' if hi < 0 else 'higher' if lo > 0 else 'overlap'}


def first_divergence(a, b, field):
    if [v['cycle_offset_us'] for v in a] != [v['cycle_offset_us'] for v in b]:
        raise ValueError('两部署完整周期不匹配')
    return next(({'cycle_offset_us': x['cycle_offset_us'], 'block1': x[field], 'block2': y[field]}
                 for x, y in zip(a, b) if x[field] != y[field]), None)


def diagnose(analysis, output, expected=INPUT_SHA256):
    began = time.monotonic_ns()
    m = bind_input(analysis, expected)
    if output.exists():
        raise ValueError('诊断输出已存在，须使用新目录')
    routes = {(r['flow']['session'], r['flow']['tag']): tuple(o['node'] for o in r['objects']) for r in m['topology']['routes']}
    rpc = rows(analysis / 'rpc-operations.jsonl')
    records, tasks, cases, objects, batches, notices, sends, traces = [], [], [], [], [], [], [], {}
    workloads = {}
    for c in m['cases']:
        r, t, case, o, b, trace, n, s = case_diagnostics(analysis / c['name'], c, rpc, routes)
        records.extend(r); tasks.extend(t); cases.append(case); objects.extend(o)
        batches.extend(b); notices.extend(n); sends.extend(s); traces[c['name']] = trace
        workload = [{k: p[k] for k in ('flow', 'offset_ns', 'ip_length', 'path')} for p in load(analysis / c['name'] / 'packets.json')]
        key = c['scenario'], c['seed']
        if key in workloads and workloads[key] != workload:
            raise ValueError('方法或部署业务输入不相同')
        workloads[key] = workload
    by_case = {c['name']: c for c in cases}
    key_cases = {(c['scenario'], c['budget_level'], c['seed'], c['block'], c['method']): c for c in cases}
    by_records, by_tasks = defaultdict(list), defaultdict(list)
    for r in records: by_records[r['name']].append(r)
    for t in tasks: by_tasks[t['name']].append(t)
    paired = []
    for original in load(analysis / 'paired-cases.json'):
        a, z = by_case[original['dynamic_case']], by_case[original['static_case']]
        pair = {k: original[k] for k in ('scenario', 'budget_level', 'seed', 'block', 'method', 'dynamic_case', 'static_case', 'both_within')}
        for observer in ('collector', 'controller'):
            pair[observer + '_difference'] = difference(a, z, observer)
            for side in ('lower', 'upper'):
                near(pair[observer + '_difference'][side], original[observer + '_difference'][side])
        pair['penalty_difference'] = {'lower': a['penalty_lower'] - z['penalty_upper'],
                                      'upper': a['penalty_upper'] - z['penalty_lower']}
        dynamic_tasks = {t['task_id']: t for t in by_tasks[a['name']]}
        static_tasks = {t['task_id']: t for t in by_tasks[z['name']]}
        if set(dynamic_tasks) != set(static_tasks):
            raise ValueError('配对任务分母不匹配')
        contributions = []
        for key, t in dynamic_tasks.items():
            s = static_tasks[key]
            if any(t[k] != s[k] for k in ('node', 'start_us', 'end_us', 'target_us', 'weight', 'denominator_fraction')):
                raise ValueError('配对任务范围或权重不匹配')
            contributions.append({'task_id': key, 'node': t['node'], 'denominator_fraction': t['denominator_fraction'],
                **{observer + '_difference_midpoint_contribution': t['denominator_fraction'] *
                   ((t[observer + '_loss_lower'] + t[observer + '_loss_upper']) -
                    (s[observer + '_loss_lower'] + s[observer + '_loss_upper'])) / 2
                   for observer in ('collector', 'controller')},
                'penalty_difference_midpoint_contribution': t['denominator_fraction'] *
                    (t['penalty_lower'] + t['penalty_upper'] - s['penalty_lower'] - s['penalty_upper']) / 2})
        pair['task_contributions'] = contributions
        paired.append(pair)
    repeats = []
    fields = ('notice_versions', 'accepted_sample_positions', 'paid_latest_counts', 'decision', 'confirmed_configuration')
    for scenario, level, seed, method in sorted({(c['scenario'], c['budget_level'], c['seed'], c['method']) for c in cases}):
        a, b = [key_cases[scenario, level, seed, block, method] for block in (1, 2)]
        aa, bb = by_records[a['name']], by_records[b['name']]
        samples = [set((r['packet_position'], r['node']) for r in group) for group in (aa, bb)]
        symmetric = sorted(samples[0] ^ samples[1])
        repeats.append({'scenario': scenario, 'budget_level': level, 'seed': seed, 'method': method,
            'cases': [a['name'], b['name']], 'initial_notice_group_mismatch': a['initial_notice_first_cycle'] != b['initial_notice_first_cycle'],
            'first_divergences': {field: first_divergence(traces[a['name']], traces[b['name']], field) for field in fields},
            'sampling_position_symmetric_difference': len(symmetric),
            'first_different_sampling_position': symmetric[0] if symmetric else None,
            'collector_repeat_difference': difference(b, a, 'collector'),
            'controller_repeat_difference': difference(b, a, 'controller')})
    grouped = defaultdict(list)
    for c in cases: grouped[c['scenario'], c['budget_level'], c['method']].append(c)
    aggregates = []
    for (scenario, level, method), members in sorted(grouped.items()):
        aggregated = {'scenario': scenario, 'budget_level': level, 'method': method, 'case_count': len(members),
                      'business_count': len({c['seed'] for c in members}),
                      'notice_first_cycle_count': sum(c['initial_notice_first_cycle'] for c in members)}
        for field in ('collector_loss_lower', 'collector_loss_upper', 'controller_loss_lower', 'controller_loss_upper',
                      'penalty_lower', 'penalty_upper', 'visible_fresh_records', 'batch_latest_records',
                      'superseded_in_batch_records', 'tail_not_read_records'):
            aggregated[field + '_mean'] = statistics.mean(c[field] for c in members)
        aggregates.append(aggregated)
    primary = [p for p in paired if p['scenario'] == 'shared_priority' and p['method'] == 'phase_guard']
    anomalies = {'budget_failures': [c for c in cases if c['window_feasibility'] != 'within'],
                 'largest_read_batches': sorted(batches, key=lambda b: b['read_ms'], reverse=True)[:10],
                 'largest_send_lateness': sorted(sends, key=lambda s: s['lateness_ms'], reverse=True)[:10],
                 'largest_collector_repeat_ranges': sorted(repeats,
                     key=lambda r: abs((r['collector_repeat_difference']['lower'] + r['collector_repeat_difference']['upper']) / 2), reverse=True)[:10],
                 'scope': '完整表均保留，前十只作索引，不是剔除阈值或已证明的根因'}
    findings = {'status': 'passed', 'cases': len(cases), 'business_count': 4, 'deployment_blocks': 2,
                'report_records': len(records), 'task_intervals': len(tasks), 'batch_objects': len(objects),
                'decision_batches': len(batches), 'visible_fresh_records': sum(c['visible_fresh_records'] for c in cases),
                'shared_cache_advances': sum(c['shared_cache_advances'] for c in cases),
                'batch_latest_records': sum(c['batch_latest_records'] for c in cases),
                'superseded_in_batch_records': sum(c['superseded_in_batch_records'] for c in cases),
                'tail_not_read_records': sum(c['tail_not_read_records'] for c in cases),
                'rejected_records': sum(c['rejected_records'] for c in cases),
                'budget_failures': len(anomalies['budget_failures']), 'paired_cases': len(paired), 'repeat_groups': len(repeats),
                'initial_notice_group_mismatches': sum(r['initial_notice_group_mismatch'] for r in repeats),
                'same_initial_notice_but_different_decisions': sum(not r['initial_notice_group_mismatch'] and
                    r['first_divergences']['decision'] is not None for r in repeats),
                'repeat_decision_divergences': sum(r['first_divergences']['decision'] is not None for r in repeats),
                'priority_phase_guard_collector_directions': dict(Counter(p['collector_difference']['direction'] for p in primary)),
                'priority_phase_guard_controller_directions': dict(Counter(p['controller_difference']['direction'] for p in primary)),
                'scope': '四个已消费业务的完整离线诊断，条件界不是置信区间；观察附加损失不是轮询干预的因果效应'}
    if (len(cases), len(paired), len(repeats), len(batches)) != (256, 224, 128, 2560):
        raise ValueError('完整诊断分母错误')
    output.mkdir(parents=True, exist_ok=False)
    for name, values in (('report-chains.csv', records), ('task-losses.csv', tasks), ('case-diagnostics.csv', cases),
                         ('object-batches.csv', objects), ('read-batches.csv', batches), ('notice-chains.csv', notices),
                         ('send-timing.csv', sends), ('method-diagnostics.csv', aggregates)):
        table(output / name, values)
    for name, value in (('paired-observers.json', paired), ('deployment-divergences.json', repeats),
                        ('decision-traces.json', traces), ('anomaly-index.json', anomalies), ('findings.json', findings)):
        write(output / name, value)
    for name in SOURCES:
        target = output / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    write(output / 'manifest.json', {'status': 'passed', 'input_manifest_sha256': expected,
          'input_directory': str(analysis.relative_to(ROOT)) if analysis.is_relative_to(ROOT) else str(analysis),
          'input_artifact_sha256': m['artifact_sha256'], 'business_seeds': m['fresh_business_seeds'],
          'new_network_runs': 0, 'new_business_inputs': 0, 'elapsed_seconds': (time.monotonic_ns() - began) / 1e9,
          'findings': findings, 'source_sha256': {n: sha(ROOT / n) for n in SOURCES},
          'artifact_sha256': {str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}})
    print(__import__('json').dumps(findings, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-manifest-sha256', default=INPUT_SHA256)
    args = parser.parse_args()
    diagnose(args.analysis.resolve(), args.output.resolve(), args.expected_manifest_sha256)
