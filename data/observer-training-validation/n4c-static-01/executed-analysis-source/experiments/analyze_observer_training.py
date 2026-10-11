"""控制器目标有限静态训练的两位置年龄、六层费用及预声明选择。"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import shutil
import statistics

from check_observer_training import audit
from observer_training_contract import select
from check_matched_network import charges_for, load, rows, sha
from check_rolling_reference import clipped_loss
from network_entry_bounds import costs, generation_bounds, output_frames

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('experiments/analyze_observer_training.py', 'experiments/check_observer_training_analysis.py',
           'experiments/observer_training_contract.py', 'experiments/network_entry_bounds.py',
           'experiments/observation_diagnostics.py', 'experiments/check_rolling_reference.py',
           'tests/test_observer_training_analysis.py')


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def table(path, values):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(values[0]), lineterminator='\n')
        writer.writeheader(); writer.writerows(values)


def controller_samples(directory, case, generation):
    origin = case['origin_monotonic_ns']
    raw = [r for r in rows(directory / 'collector.jsonl') if r.get('ether_type') == 0x88b6]
    intervals = {(r['packet_id'], r['node']): r for r in generation['intervals']}
    available, samples, latest = [], [], {}
    for observed in rows(directory / 'report-observations.jsonl'):
        if observed['skipped']: continue
        e = observed['availability']
        for report in observed['fresh_reports']:
            def matches(r):
                records = [{'node': x['switch_id'], 'hop': x['hop'], 'generated_local_us': x['generated_us'],
                            'queue_packets': x['queue_packets']} for x in r['records']]
                return (r['receive_monotonic_ns'] - origin) // 1000 == report['received_us'] and \
                    (r['session'], r['flow_tag']) == (report['flow']['session'], report['flow']['tag']) and \
                    all(x in records for x in report['records'])
            candidates = [r for r in raw if matches(r)]
            if not candidates: raise ValueError('实际观察的新报告没有线格式来源')
            physical = min(candidates, key=lambda r: r['receive_monotonic_ns'])
            available.append({'case': case['name'], 'method': case['method'], 'packet_id': physical['packet_id'],
                'observation_index': observed['observation_index'], 'received_ns': physical['receive_monotonic_ns'],
                'entered_lower_ns': e['ingest_started_ns'], 'entered_upper_ns': e['available_ns'],
                'receipt_to_entry_upper_ms': (e['available_ns'] - physical['receive_monotonic_ns']) / 1e6})
            for record in report['records']:
                node, generated = record['node'], record['generated_local_us']
                old = latest.get(node)
                if old is not None and not 0 < (generated - old) % 2**48 < 2**47: continue
                latest[node] = generated
                interval = intervals[physical['packet_id'], node]
                samples.append({'node': node, 'packet_id': physical['packet_id'], 'generated_local_us': generated,
                    'observation_index': observed['observation_index'], 'received_ns': physical['receive_monotonic_ns'],
                    'generation_lower_ns': interval['generation_lower_ns'], 'generation_upper_ns': interval['generation_upper_ns'],
                    'entered_lower_ns': e['ingest_started_ns'], 'entered_upper_ns': e['available_ns']})
    return available, samples


def age_bounds(tasks, samples, origin):
    sides = (('lower', 'generation_upper_ns', 'entered_lower_ns'), ('upper', 'generation_lower_ns', 'entered_upper_ns'))
    result = {}
    for side, generated, entered in sides:
        receipts = [{'object_id': {'node': r['node'], 'port': 2, 'queue': 0},
                     'generated_us': (r[generated] - origin) / 1000,
                     'received_us': (r[entered] - origin) / 1000} for r in samples]
        result['loss_' + side], result['weighted_duration_us'] = clipped_loss(tasks, receipts, 4)
    if result['loss_lower'] > result['loss_upper'] + 1e-12: raise ValueError('控制器年龄条件界倒序')
    return dict(result, sample_count=len(samples), scope='全部真实观察批次首次推进的共享缓存；含活跃未知区间，非置信区间或硬同步')


def analyze(run, output):
    verification = audit(run)
    manifest, rpc = load(run / 'manifest.json'), rows(run / 'rpc-operations.jsonl')
    outputs = output_frames(run, manifest['cases'])
    output.mkdir(parents=True, exist_ok=False)
    summaries, all_available, batches = [], [], []
    for case in manifest['cases']:
        directory, target = run / case['name'], output / case['name']; target.mkdir()
        for p in directory.iterdir():
            if p.suffix in ('.json', '.jsonl', '.pcap'): shutil.copyfile(p, target / p.name)
        generation = generation_bounds(directory, case)
        cost = costs(directory, case, outputs[case['name']], charges_for(directory, rpc))
        available, samples = controller_samples(directory, case, generation)
        visible = age_bounds(load(directory / 'tasks.json'), samples, case['origin_monotonic_ns'])
        for name, value in (('generation-bounds.json', generation), ('window-costs.json', cost),
                            ('controller-samples.json', samples), ('controller-visible-bounds.json', visible)):
            write(target / name, value)
        table(target / 'output-frames.csv', outputs[case['name']]); all_available.extend(available)
        observations = rows(directory / 'report-observations.jsonl')
        completed = [r for r in observations if not r['skipped']]
        for r in completed:
            e = r['availability']
            batches.append({'case': case['name'], 'method': case['method'], 'observation_index': r['observation_index'],
                'read_ms': (e['read_finished_ns'] - e['read_started_ns']) / 1e6,
                'ingest_ms': (e['available_ns'] - e['ingest_started_ns']) / 1e6,
                'input_rows': e['row_count'], 'new_reports': len(r['fresh_reports']), 'lateness_ms': r['lateness_ns'] / 1e6})
        bs = [b for b in batches if b['case'] == case['name']]
        decisions = rows(directory / 'decisions.jsonl')
        solver = [(d['solver_finished_ns'] - d['solver_started_ns']) / 1e6 for d in decisions if 'solver_started_ns' in d]
        own = [r for r in rpc[case['rpc_start_index']:case['rpc_stop_index']] if r['kind'] in ('update', 'online_observation')]
        notices = rows(directory / 'notices.jsonl')
        host = load(directory / 'host.json')
        summary = {'case': case['name'], 'scenario': case['scenario'], 'budget_level': case['budget_level'], 'method': case['method'],
            'seed': case['seed'], 'candidate_index': case['candidate_index'], 'position': case['position'], 'packets': case['packet_count'], 'reports': case['report_count'],
            'completed_observations': len(completed), 'skipped_observations': len(observations) - len(completed),
            'decisions': len(decisions), 'skipped_decisions': sum(d.get('skipped', False) for d in decisions),
            'paid_counter_calls': sum(r['kind'] == 'online_observation' for r in own), 'successful_updates': case['successful_updates'],
            'visible_reports': len(available), 'shared_cache_advances': len(samples), 'unread_tail_reports': case['report_count'] - len(available),
            'collector_loss_lower': generation['loss_lower'], 'collector_loss_upper': generation['loss_upper'],
            'controller_loss_lower': visible['loss_lower'], 'controller_loss_upper': visible['loss_upper'],
            'window_feasibility': cost['window_feasibility'], 'wall_clock_compatible': cost['wall_clock']['compatible'],
            'generation_clock_compatible': all(v['compatible'] is not False for v in generation['local_clock_offset'].values()),
            'api_bytes_per_s': cost['api_bytes_per_s'], 'local_notice_bytes_per_s': cost['local_notice_bytes_per_s'],
            'receipt_to_entry_upper_max_ms': max((r['receipt_to_entry_upper_ms'] for r in available), default=None),
            'read_max_ms': max(r['read_ms'] for r in bs), 'ingest_max_ms': max(r['ingest_ms'] for r in bs),
            'observation_max_lateness_ms': max(r['lateness_ns'] / 1e6 for r in observations),
            'solver_max_ms': max(solver, default=None), 'rpc_max_ms': max(((r['after_monotonic_ns'] - r['before_monotonic_ns']) / 1e6 for r in own), default=None),
            'sender_max_lateness_ms': max(r['schedule_lateness_ns'] / 1e6 for r in rows(directory / 'send.jsonl')),
            'initial_notice_first_cycle': bool(notices and any(n['version'] == notices[0]['message']['version'] for n in decisions[0].get('view', {}).get('notifications', []))),
            'controller_mean_cpu_cores': host['usage']['controller']['mean_cpu_cores'],
            'controller_rss_start_bytes': host['usage']['controller']['rss_start_bytes'], 'controller_rss_end_bytes': host['usage']['controller']['rss_end_bytes']}
        for resource in ('e1', 'e2', 'e3', 'export'):
            for side in ('lower', 'upper'):
                v = cost[f'measurement_{side}_bytes']
                summary[f'{resource}_{side}_bytes_per_s'] = v[resource] / 8 if v is not None else None
        summaries.append(summary)
    table(output / 'summary.csv', summaries); table(output / 'report-availability.csv', all_available)
    table(output / 'observation-batches.csv', batches)
    for folder in run.glob('node-*'):
        target = output / folder.name; target.mkdir()
        for p in folder.glob('*_out.pcap'): shutil.copyfile(p, target / p.name)
    for name in ('rpc-operations.jsonl', 'rpc.jsonl', 'rpc.pcap', 'unit-tests.txt', 'frozen-inputs.json',
                 'training-plan.json', 'candidate-plan.json', 'protocol.json', 'seed-audit.json', 'method-contract.json', 'execution-order.json'):
        shutil.copyfile(run / name, output / name)
    if any(r['socket_drop_counter'] for r in rows(run / 'rpc.jsonl')): raise ValueError('控制帧捕获有套接字丢包')
    shutil.copytree(run / 'executed-source', output / 'executed-source')
    for name in SOURCES:
        target = output / 'executed-analysis-source' / name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    verification['output_frames'] = sum(map(len, outputs.values())); write(output / 'checks.json', verification)
    findings = {'cases': len(summaries), 'business_seed_count': 2, 'packets': verification['packet_count'],
        'reports': verification['report_count'], 'output_frames': verification['output_frames'], 'decisions': verification['decision_count'],
        'observation_slots': verification['observation_slots'], 'completed_observations': verification['completed_observations'],
        'skipped_observations': verification['skipped_observations'], 'visible_reports': len(all_available),
        'shared_cache_advances': verification['shared_cache_advances'], 'successful_updates': verification['successful_updates'],
        'paid_counter_calls': sum(r['paid_counter_calls'] for r in summaries),
        'window_feasibility': dict(Counter(r['window_feasibility'] for r in summaries)),
        'report_receipt_to_entry_upper_max_ms': max(r['receipt_to_entry_upper_ms'] for r in all_available),
        'read_max_ms': max(r['read_ms'] for r in batches), 'ingest_max_ms': max(r['ingest_ms'] for r in batches),
        'observer_max_lateness_ms': max(r['observation_max_lateness_ms'] for r in summaries),
        'solver_max_ms': max(r['solver_max_ms'] for r in summaries if r['solver_max_ms'] is not None),
        'rpc_max_ms': max((r['rpc_max_ms'] for r in summaries if r['rpc_max_ms'] is not None), default=None),
        'sender_max_lateness_ms': max(r['sender_max_lateness_ms'] for r in summaries),
        'initial_notice_first_cycle_cases': sum(r['initial_notice_first_cycle'] for r in summaries),
        'scope': '24/6561有限候选、11/29已消费双训练业务，按控制器目标选择；非全网格最优、新测试或统计优势'}
    scores, selections = select(load(run / 'training-plan.json'), summaries)
    write(output / 'candidate-scores.json', scores)
    write(output / 'training-selections.json', selections)
    write(output / 'finite-frontier.json', [r for r in scores if r['finite_subset_nondominated']])
    findings['feasible_candidates'] = {r['scenario']: r['feasible_candidates'] for r in selections}
    findings['selected_candidates'] = {r['scenario']: r['selected_candidate'] for r in selections}
    findings['changed_training_selections'] = [r['scenario'] for r in selections if r['selection_changed']]
    findings['finite_frontier_candidates'] = {r['scenario']: sum(s['scenario'] == r['scenario'] and s['finite_subset_nondominated'] for s in scores) for r in selections}
    write(output / 'findings.json', findings)
    record = dict(manifest, findings=findings, raw_manifest_sha256=sha(run / 'manifest.json'),
        raw_artifact_sha256=manifest['artifact_sha256'], analysis_source_sha256={n: sha(ROOT / n) for n in SOURCES},
        artifact_sha256={str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()
                         and 'executed-source' not in p.parts and 'executed-analysis-source' not in p.parts})
    write(output / 'manifest.json', record)
    print(json.dumps(findings, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); analyze(a.run, a.output)
