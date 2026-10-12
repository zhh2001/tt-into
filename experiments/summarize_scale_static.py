"""汇总有限静态训练的预算资格与实际软件观察负担。"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from scalable_contract import sha
from check_observation_wire import load, rows
from check_rolling_reference import same


def summarize(analysis):
    summary = load(analysis / 'summary.json')
    source = load(analysis / 'source-manifest.json')
    scores = load(analysis / 'candidate-scores.json')
    selected = load(analysis / 'selected-candidates.json')
    same(len(summary), 960, '完整静态训练分母')
    same(len(scores), 480, '完整双业务候选分母')
    cases = {(b['batch_id'], c['name']): c for b in source['batches'] for c in b['cases']}
    same(len(cases), len(summary), '实际案例与汇总分母')
    maxima = {}
    skipped = []
    failures = []
    clocks = Counter()
    task_intervals = visible_reports = first_notices = 0

    def maximum(name, value, case, **location):
        if value is not None and (name not in maxima or value > maxima[name]['value_ms']):
            maxima[name] = dict(value_ms=value, case=case, **location)

    for row in summary:
        case = cases[row['batch_id'], row['case']]
        directory = analysis / row['batch_id'] / row['case']
        task_intervals += len(load(directory / 'tasks.json'))
        visible_reports += case['visible_reports']
        maximum('report_to_view_upper', row['observer_max_delay_ms'], row['case'])
        observations = rows(directory / 'report-observations.jsonl')
        for observed in observations:
            maximum('observation_lateness', observed['lateness_ns'] / 1e6, row['case'],
                    observation_index=observed['observation_index'])
            event = observed.get('availability')
            if event:
                maximum('report_read', (event['read_finished_ns'] - event['read_started_ns']) / 1e6,
                        row['case'], observation_index=observed['observation_index'])
                maximum('cache_update', (event['available_ns'] - event['ingest_started_ns']) / 1e6,
                        row['case'], observation_index=observed['observation_index'])
        if case['skipped_observations']:
            skipped.append(dict(case=row['case'], count=case['skipped_observations'],
                warmup=sum(o['skipped'] and o['scheduled_offset_us'] < 2000000 for o in observations),
                measurement=sum(o['skipped'] and o['scheduled_offset_us'] >= 2000000 for o in observations)))
        decisions = rows(directory / 'decisions.jsonl')
        first_notices += bool(decisions[0].get('view', {}).get('notifications'))
        for decision in decisions:
            if 'solver_started_ns' in decision:
                maximum('static_solver', (decision['solver_finished_ns'] - decision['solver_started_ns']) / 1e6,
                        row['case'], scheduled_offset_us=decision['scheduled_offset_us'])
        for sent in rows(directory / 'send.jsonl'):
            maximum('source_lateness', sent['schedule_lateness_ns'] / 1e6, row['case'], packet_id=sent['packet_id'])
        cost = load(directory / 'window-costs.json')
        clocks['wall_compatible' if cost['wall_clock']['compatible'] else 'wall_incompatible'] += 1
        generation = load(directory / 'generation-bounds.json')
        for clock in generation['local_clock_offset'].values():
            clocks['generation_unknown' if clock['compatible'] is None else
                   'generation_compatible' if clock['compatible'] else 'generation_incompatible'] += 1
        if row['window_feasibility'] != 'within':
            limits = dict(case['budget']['link_bytes_per_s'], export=case['budget']['export_bytes_per_s'])
            violated = [] if cost['measurement_lower_bytes'] is None else [
                dict(resource=k, lower_bytes_per_s=cost['measurement_lower_bytes'][k] / 8,
                     upper_bytes_per_s=cost['measurement_upper_bytes'][k] / 8, limit_bytes_per_s=v)
                for k, v in limits.items() if cost['measurement_lower_bytes'][k] / 8 > v]
            failures.append(dict(case=row['case'], status=row['window_feasibility'], violated_resources=violated))

    cells = []
    for choice in selected:
        group = [r for r in summary if (r['network_id'], r['scenario']) ==
                 (choice['network_id'], choice['scenario'])]
        same(len(group), 96, '每单元完整候选和业务')
        cells.append(dict(network_id=choice['network_id'], scenario=choice['scenario'],
            cases=len(group), budget_states=dict(Counter(r['window_feasibility'] for r in group)),
            eligible_candidates=choice['eligible_candidates'],
            selected=[dict(candidate_id=r['candidate_id'], worst_controller_upper=r['worst_controller_upper'],
                mean_controller_upper=r['mean_controller_upper'], mean_export_bytes_per_s=r['mean_export_bytes_per_s'])
                for r in choice['selected']]))
    return dict(status='passed', role='既有11/29训练业务的有限静态选择',
        totals=dict(cases=len(summary), task_intervals=task_intervals, visible_reports=visible_reports,
            reports_outside_view=sum(r['reports'] for r in summary) - visible_reports,
            first_notice_available=first_notices,
            budget_states=dict(Counter(r['window_feasibility'] for r in summary))),
        cells=cells, software_maxima=maxima, skipped_cases=skipped, budget_failures=failures,
        clock_sets=dict(clocks), concurrent_activity=source['concurrent_activity'],
        scope='100ms共同观察的实际软件诊断。条件年龄界不是统计置信区间。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.analysis)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'diagnostics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    table = [dict(network_id=c['network_id'], scenario=c['scenario'], eligible_candidates=c['eligible_candidates'],
                  within_cases=c['budget_states'].get('within', 0), **r) for c in result['cells'] for r in c['selected']]
    with (args.output / 'selected-summary.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(table[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(table)
    manifest = dict(status='passed', script_sha256=sha(Path(__file__)),
        analysis_manifest_sha256=sha(args.analysis / 'manifest.json'),
        artifact_sha256={p.name: sha(p) for p in args.output.iterdir() if p.is_file()})
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(result['totals']))
