"""汇总实际可用性及完整案例之间的开发波动，不把包当独立重复。"""
from collections import defaultdict
import csv
import json
import math
import statistics

from check_matched_network import load, rows
from check_rolling_reference import clipped_loss


def quantile(values, fraction):
    values = sorted(values)
    if not values or not 0 < fraction <= 1:
        raise ValueError('观察分位数输入错误')
    return values[max(0, math.ceil(len(values) * fraction) - 1)]


def table(output, name, values):
    with (output / name).open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(values[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(values)


def summarize(run, output, cases, summaries):
    batches, available, notices, per_case = [], [], [], []
    for case in cases:
        directory = run / case['name']
        origin = case['origin_monotonic_ns']
        raw = rows(directory / 'collector.jsonl')
        decisions = [d for d in rows(directory / 'decisions.jsonl') if 'view' in d]
        identity = {k: case[k] for k in ('name', 'scenario', 'budget_level', 'method', 'block')}
        for d in decisions:
            e = d['availability']
            batch = dict(identity, scheduled_offset_us=d['scheduled_offset_us'],
                         read_ms=(e['read_finished_ns'] - e['read_started_ns']) / 1e6,
                         ingest_ms=(e['available_ns'] - e['ingest_started_ns']) / 1e6,
                         cutoff_slack_ms=(d['cutoff_ns'] - e['available_ns']) / 1e6,
                         input_rows=e['row_count'], newly_accepted=e['reports_after'] - e['reports_before'])
            batches.append(batch)
            for report in d['view']['reports'][e['reports_before']:e['reports_after']]:
                matches = [r for r in raw if r.get('ether_type') == 0x88b6 and
                           (r['receive_monotonic_ns'] - origin) // 1000 == report['received_us'] and
                           (r['session'], r['flow_tag']) == (report['flow']['session'], report['flow']['tag']) and
                           [{'node': x['switch_id'], 'hop': x['hop'], 'generated_local_us': x['generated_us'],
                             'queue_packets': x['queue_packets']} for x in r['records']] == report['records']]
                if not matches:
                    raise ValueError('可用性汇总报告没有原始收报来源')
                r = min(matches, key=lambda value: value['receive_monotonic_ns'])
                if r['receive_monotonic_ns'] > e['read_finished_ns']:
                    raise ValueError('可用性汇总提前使用未来报告')
                available.append(dict(identity, packet_id=r['packet_id'], received_ns=r['receive_monotonic_ns'],
                                      entered_lower_ns=e['ingest_started_ns'], entered_upper_ns=e['available_ns'], first_cutoff_ns=d['cutoff_ns'],
                                      receipt_to_entry_upper_ms=(e['available_ns'] - r['receive_monotonic_ns']) / 1e6,
                                      scheduled_offset_us=d['scheduled_offset_us']))
        generation = load(output / case['name'] / 'generation-bounds.json')
        intervals = {(r['packet_id'], r['node']): r for r in generation['intervals']}
        earlier, later = [], []
        for r in [a for a in available if a['name'] == case['name']]:
            physical = next(x for x in raw if x.get('ether_type') == 0x88b6 and x['packet_id'] == r['packet_id'])
            for record in physical['records']:
                interval = intervals[r['packet_id'], record['switch_id']]
                obj = {'node': record['switch_id'], 'port': 2, 'queue': 0}
                earlier.append({'object_id': obj, 'generated_us': (interval['generation_upper_ns'] - origin) / 1000,
                                'received_us': (r['entered_lower_ns'] - origin) / 1000})
                later.append({'object_id': obj, 'generated_us': (interval['generation_lower_ns'] - origin) / 1000,
                              'received_us': (r['entered_upper_ns'] - origin) / 1000})
        tasks = load(directory / 'tasks.json')
        visible_low, visible_high = clipped_loss(tasks, earlier, 4)[0], clipped_loss(tasks, later, 4)[0]
        if visible_low > visible_high + 1e-12:
            raise ValueError('控制器视图的年龄区间倒序')
        (output / case['name'] / 'controller-visible-bounds.json').write_text(json.dumps({
            'loss_lower': visible_low, 'loss_upper': visible_high, 'sample_count': len(earlier),
            'scope': '实际状态更新区间与生成时间条件界的控制器视图 AoI，与采集端实收 AoI 分列'}, ensure_ascii=False, indent=2) + '\n')
        sends = {r['version']: r for r in rows(directory / 'notice-send.jsonl')}
        plans = {r['message']['version']: r for r in load(directory / 'notice-schedule.json')}
        for n in rows(directory / 'notices.jsonl'):
            version = n['message']['version']
            first = next(d for d in decisions if any(x['version'] == version for x in d['view']['notifications']))
            sent, plan = sends[version], plans[version]
            notices.append(dict(identity, version=version, released_offset_us=plan['offset_us'],
                                send_started_ns=sent['before_ns'], send_finished_ns=sent['after_ns'],
                                recv_started_ns=n['receive_started_ns'], recv_finished_ns=n['received_ns'],
                                first_cutoff_ns=first['cutoff_ns'], first_cycle_offset_us=first['scheduled_offset_us'],
                                release_to_receive_ms=(n['received_ns'] - origin - plan['offset_us'] * 1000) / 1e6,
                                send_finish_to_recv_finish_ms=(n['received_ns'] - sent['after_ns']) / 1e6))
        bs = [r for r in batches if r['name'] == case['name']]
        rs = [r['receipt_to_entry_upper_ms'] for r in available if r['name'] == case['name']]
        own_notices = [r for r in notices if r['name'] == case['name']]
        summary = next(r for r in summaries if r['case'] == case['name'])
        per_case.append(dict(identity, initial_notice_first_cycle=bool(own_notices and own_notices[0]['first_cycle_offset_us'] == 0),
                             read_mean_ms=statistics.mean(r['read_ms'] for r in bs),
                             read_max_ms=max(r['read_ms'] for r in bs),
                             ingest_mean_ms=statistics.mean(r['ingest_ms'] for r in bs),
                             accepted_visible_reports=len(rs),
                             receipt_to_entry_upper_p50_ms=quantile(rs, .5) if rs else None,
                             receipt_to_entry_upper_p95_ms=quantile(rs, .95) if rs else None,
                             receipt_to_entry_upper_max_ms=max(rs) if rs else None,
                             solver_mean_ms=summary['solver_mean_ms'],
                             loss_lower=summary['loss_lower'], loss_upper=summary['loss_upper'],
                             controller_loss_lower=visible_low, controller_loss_upper=visible_high,
                             window_feasibility=summary['window_feasibility']))
    groups = defaultdict(list)
    for r in per_case:
        groups[r['scenario'], r['budget_level'], r['method']].append(r)
    aggregate = []
    for (scenario, level, method), values in groups.items():
        lower, upper = [r['loss_lower'] for r in values], [r['loss_upper'] for r in values]
        solver = [r['solver_mean_ms'] for r in values]
        aggregate.append({'scenario': scenario, 'budget_level': level, 'method': method,
                          'case_count': len(values), 'business_seed_count': 1,
                          'initial_notice_first_cycle_cases': sum(r['initial_notice_first_cycle'] for r in values),
                          'within_cases': sum(r['window_feasibility'] == 'within' for r in values),
                          'loss_lower_mean': statistics.mean(lower), 'loss_upper_mean': statistics.mean(upper),
                          'loss_observed_lower_min': min(lower), 'loss_observed_upper_max': max(upper),
                          'controller_loss_lower_mean': statistics.mean(r['controller_loss_lower'] for r in values),
                          'controller_loss_upper_mean': statistics.mean(r['controller_loss_upper'] for r in values),
                          'loss_midpoint_case_range': max((a+b)/2 for a,b in zip(lower,upper)) - min((a+b)/2 for a,b in zip(lower,upper)),
                          'solver_case_mean_min_ms': min(solver), 'solver_case_mean_max_ms': max(solver)})
    for name, records in (('availability-batches.csv', batches), ('report-availability.csv', available),
                          ('notice-availability.csv', notices), ('case-availability.csv', per_case), ('block-aggregate.csv', aggregate)):
        table(output, name, records)
    result = {'blocks': max(c['block'] for c in cases), 'availability_batches': len(batches),
              'visible_fresh_reports': len(available), 'initial_notice_first_cycle_cases': sum(r['initial_notice_first_cycle'] for r in per_case),
              'read_max_ms': max(r['read_ms'] for r in batches),
              'report_receipt_to_entry_upper_max_ms': max(r['receipt_to_entry_upper_ms'] for r in available),
              'largest_loss_midpoint_case_range': max(r['loss_midpoint_case_range'] for r in aggregate),
              'availability_scope': 'recvmsg 完成至本批状态更新结束的观察上界，非内核/线上物理接收到达精度；按完整案例评价变异'}
    (output / 'availability-findings.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result
