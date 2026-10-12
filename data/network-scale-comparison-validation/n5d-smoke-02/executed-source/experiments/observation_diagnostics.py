"""从实际完整前缀重建报告、共享缓存与任务年龄的诊断链。"""
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math

INPUT_SHA256 = '1c8084770ecb68faa73d485489dfed84475c661abb1eb40f3f43b8050e3b292a'
CASE_FILES = ('result.json', 'packets.json', 'tasks.json', 'collector.jsonl',
              'generation-bounds.json', 'controller-visible-bounds.json', 'decisions.jsonl',
              'availability.jsonl', 'ingestion.jsonl', 'actions.jsonl', 'notices.jsonl',
              'notice-send.jsonl', 'notice-schedule.json', 'send.jsonl', 'window-costs.json')


def load(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def table(path, values):
    if not values:
        raise ValueError('完整诊断表不可为空')
    with path.open('w', newline='') as stream:
        out = csv.DictWriter(stream, fieldnames=list(values[0]), lineterminator='\n')
        out.writeheader()
        out.writerows(values)


def near(a, b):
    if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError('诊断重建与已执行年龄不符')


def bind_input(analysis, expected=INPUT_SHA256):
    if sha(analysis / 'manifest.json') != expected:
        raise ValueError('完整比较清单摘要不符')
    m = load(analysis / 'manifest.json')
    if m['status'] != 'passed' or len(m['cases']) != 256 or m['fresh_business_seeds'] != [3001, 3002, 3003, 3004]:
        raise ValueError('要求完整已消费四业务两块比较')
    counts = Counter((c['scenario'], c['budget_level'], c['seed'], c['block'], c['method']) for c in m['cases'])
    if len(counts) != 256 or any(v != 1 for v in counts.values()):
        raise ValueError('重复案例身份')
    required = ['rpc-operations.jsonl', 'paired-cases.json', 'summary-checks.json', 'comparison-plan.json']
    required += [c['name'] + '/' + n for c in m['cases'] for n in CASE_FILES]
    for name in required:
        if name not in m['artifact_sha256']:
            raise ValueError('诊断输入缺少原始摘要')
    for name, digest in m['artifact_sha256'].items():
        if sha(analysis / name) != digest:
            raise ValueError('比较产物来源摘要不符: ' + name)
    for key, prefix in (('source_sha256', 'executed-source'), ('analysis_source_sha256', 'executed-analysis-source')):
        for name, digest in m[key].items():
            if sha(analysis / prefix / name) != digest:
                raise ValueError('已执行源码摘要不符: ' + name)
    if load(analysis / 'summary-checks.json')['status'] != 'passed':
        raise ValueError('输入没有通过独立分析检查')
    return m


def advances(new, old):
    return old is None or 0 < (new - old) % 2**48 < 2**47


def identity(row):
    return (row['session'], row['flow_tag'], tuple((r['switch_id'], r['hop'], r['generated_us'],
                                                  r['queue_packets']) for r in row['records']))


def report_chain(raw, decisions, origin, routes):
    """独立保留重复/旧记录与未进入实际周期的尾段，不调用在线状态类。"""
    lines = raw.splitlines(keepends=True)
    physical = [json.loads(line) for line in lines if line.strip()]
    if len(physical) != len(lines):
        raise ValueError('正式记录存在空行')
    chains, seen, latest, cached, reports, batches = {}, set(), {}, {}, [], []
    events = []
    for d in decisions:
        if 'view' not in d:
            raise ValueError('本轮不支持缺失实际视图的周期')
        e = d['availability']
        times = [d['cycle_started_ns'], e['read_started_ns'], e['read_finished_ns'],
                 e['ingest_started_ns'], e['available_ns'], d['cutoff_ns'], d['solver_started_ns']]
        if times != sorted(times) or e['cutoff_ns'] != d['cutoff_ns']:
            raise ValueError('读取、更新与决策时序错误')
        if not 0 <= e['prefix_bytes'] <= e['file_bytes'] <= len(raw):
            raise ValueError('完整前缀长度错误')
        prefix = raw[:e['file_bytes']]
        prefix = prefix[:prefix.rfind(b'\n') + 1]
        if len(prefix) != e['prefix_bytes'] or hashlib.sha256(prefix).hexdigest() != e['prefix_sha256']:
            raise ValueError('实际完整前缀摘要错误')
        batch = [json.loads(line) for line in prefix.splitlines() if line]
        if len(batch) != e['row_count'] or len(reports) != e['reports_before']:
            raise ValueError('读取批次分母错误')
        accepted, moved = [], []
        for index, row in enumerate(batch):
            if row.get('receive_monotonic_ns', e['read_finished_ns']) > e['read_finished_ns']:
                raise ValueError('读取包含未来报告')
            if row.get('ether_type') != 0x88b6 or index in chains:
                continue
            flow = (row['session'], row['flow_tag'])
            duplicate = identity(row) in seen
            seen.add(identity(row))
            route = routes.get(flow)
            valid = row['ip_checksum_ok'] and row['records'] and route is not None and all(
                1 <= r['hop'] <= len(route) and route[r['hop'] - 1] == r['switch_id'] for r in row['records'])
            fresh, records = [], []
            for record in row['records']:
                node, stamp = record['switch_id'], record['generated_us']
                before = latest.get((flow, node))
                is_fresh = bool(not duplicate and valid and advances(stamp, before))
                cache_advance = bool(is_fresh and advances(stamp, cached.get(node, {}).get('generated_local_us')))
                status = 'duplicate' if duplicate else 'invalid_report' if not valid else 'fresh' if is_fresh else 'old_record'
                detail = {'node': node, 'fresh_per_flow': is_fresh, 'advances_shared_cache': cache_advance,
                          'status': status, 'batch_latest': False}
                records.append(detail)
                if is_fresh:
                    latest[flow, node] = stamp
                    fresh.append({'node': node, 'hop': record['hop'], 'generated_local_us': stamp,
                                  'queue_packets': record['queue_packets']})
                    accepted.append((index, node))
                if cache_advance:
                    cached[node] = {'object_id': {'node': node, 'port': 2, 'queue': 0},
                                    'generated_local_us': stamp,
                                    'received_us': (row['receive_monotonic_ns'] - origin) // 1000,
                                    'queue_packets': record['queue_packets']}
                    moved.append((index, node))
            chains[index] = {'row': index, 'read_finished_ns': e['read_finished_ns'],
                             'entered_lower_ns': e['ingest_started_ns'], 'entered_upper_ns': e['available_ns'],
                             'cutoff_ns': d['cutoff_ns'], 'cycle_offset_us': d['scheduled_offset_us'], 'records': records}
            if fresh:
                reports.append({'received_us': (row['receive_monotonic_ns'] - origin) // 1000,
                                'flow': {'session': flow[0], 'tag': flow[1]}, 'records': fresh})
        for node in {node for _, node in moved}:
            last = [index for index, n in moved if n == node][-1]
            next(r for r in chains[last]['records'] if r['node'] == node)['batch_latest'] = True
        if len(reports) != e['reports_after'] or reports != d['view']['reports']:
            raise ValueError('合法报告累计前缀不符')
        if [cached[n] for n in sorted(cached)] != d['view']['cache']:
            raise ValueError('共享缓存被重复或旧记录重置')
        events.append(e)
        batches.append({'cycle_offset_us': d['scheduled_offset_us'], 'accepted_records': accepted,
                        'cache_advances': moved, 'cache': {n: dict(v) for n, v in cached.items()}})
    for index, row in enumerate(physical):
        if row.get('ether_type') == 0x88b6 and index not in chains:
            chains[index] = {'row': index, 'read_finished_ns': None, 'entered_lower_ns': None,
                             'entered_upper_ns': None, 'cutoff_ns': None, 'cycle_offset_us': None,
                             'records': [{'node': r['switch_id'], 'fresh_per_flow': None,
                                          'advances_shared_cache': None, 'status': 'not_read_in_cycles',
                                          'batch_latest': None} for r in row['records']]}
    return physical, chains, batches, events


def task_integral(task, samples, origin, generated_side, observer_side, clip=4):
    """纳秒事件按实际到达推进最大生成时刻，解析积分保留未知时段。"""
    start, end = origin + task['start_us'] * 1000, origin + task['end_us'] * 1000
    target = task['target_us'] * 1000
    if start >= end or target <= 0 or task['weight'] <= 0:
        raise ValueError('任务区间、目标或权重非法')
    events = defaultdict(list)
    for sample in samples:
        if sample['node'] == task['object_id']['node'] and sample[observer_side] is not None:
            at, gen = sample[observer_side], sample[generated_side]
            if gen > at:
                raise ValueError('生成晚于观察可用')
            events[at].append(gen)
    latest = max((g for at, gs in events.items() if at <= start for g in gs), default=None)
    points = sorted({start, end} | {at for at in events if start < at < end})
    area, unknown = 0.0, 0
    for a, b in zip(points, points[1:]):
        if a in events:
            latest = max(events[a] + ([] if latest is None else [latest]))
        if latest is None:
            area += clip * (b - a)
            unknown += b - a
        else:
            limit = latest + clip * target
            split = min(b, max(a, limit))
            area += ((a - latest) + (split - latest)) * (split - a) / (2 * target)
            area += clip * (b - split)
    return area / (end - start), unknown


def configuration_at(actions, rpc, flow, node, lower, upper):
    state = {}
    relevant = [a for a in actions if a['node'] == node and (a['flow']['session'], a['flow']['tag']) == flow]
    for a in relevant:
        if a['success'] and a['confirmation_available_ns'] <= lower:
            state[a['field']] = a
    overlap = [a for a in relevant if a['success'] and
               rpc[a['rpc_index']]['before_monotonic_ns'] <= upper and rpc[a['rpc_index']]['after_monotonic_ns'] >= lower]
    unconfirmed = [a for a in relevant if a['success'] and rpc[a['rpc_index']]['before_monotonic_ns'] <= upper
                   and a['confirmation_available_ns'] > lower]
    threshold, enabled = state.get('threshold_us'), state.get('enabled')
    return {'confirmed_threshold_us': None if threshold is None else threshold['value'],
            'confirmed_enabled': None if enabled is None else int(enabled['value']),
            'configuration_interval_overlaps': len(overlap), 'configuration_not_yet_confirmed': len(unconfirmed),
            'last_confirmation_ns': max((a['confirmation_available_ns'] for a in state.values()), default=None)}


def case_diagnostics(directory, case, rpc, routes):
    origin = case['origin_monotonic_ns']
    raw = (directory / 'collector.jsonl').read_bytes()
    decisions = rows(directory / 'decisions.jsonl')
    physical, chain, batches, events = report_chain(raw, decisions, origin, routes)
    if events != rows(directory / 'availability.jsonl'):
        raise ValueError('完整更新事件留档不符')
    packets = load(directory / 'packets.json')
    position = {p['packet_id']: index for index, p in enumerate(packets)}
    if len(position) != len(packets):
        raise ValueError('离线业务包身份重复')
    intervals = {(r['packet_id'], r['node']): r for r in load(directory / 'generation-bounds.json')['intervals']}
    actions = rows(directory / 'actions.jsonl')
    common = {k: case[k] for k in ('name', 'scenario', 'budget_level', 'method', 'seed', 'block')}
    records, lookup = [], {}
    for index, part in chain.items():
        row = physical[index]
        pid = row['packet_id']
        if pid not in position:
            raise ValueError('报告缺少业务包来源')
        for detail, wire in zip(part['records'], row['records']):
            node = detail['node']
            g = intervals[pid, node]
            lo, hi = g['generation_lower_ns'], g['generation_upper_ns']
            if lo > hi or hi > row['receive_monotonic_ns']:
                raise ValueError('生成条件界不合法')
            first = part['entered_upper_ns']
            flow = (row['session'], row['flow_tag'])
            record = dict(common, collector_row=index, packet_id=pid, packet_position=position[pid],
                          flow=packets[position[pid]]['flow'], session=flow[0], flow_tag=flow[1],
                          node=node, local_generated_us=wire['generated_us'],
                          generation_lower_ns=lo, generation_upper_ns=hi, received_ns=row['receive_monotonic_ns'],
                          read_finished_ns=part['read_finished_ns'], entered_lower_ns=part['entered_lower_ns'],
                          entered_upper_ns=first, first_cutoff_ns=part['cutoff_ns'],
                          first_cycle_offset_us=part['cycle_offset_us'], **{k: v for k, v in detail.items() if k != 'node'},
                          receipt_to_entry_lower_ms=None if first is None else (part['entered_lower_ns'] - row['receive_monotonic_ns']) / 1e6,
                          receipt_to_entry_upper_ms=None if first is None else (first - row['receive_monotonic_ns']) / 1e6,
                          generation_to_receipt_lower_ms=(row['receive_monotonic_ns'] - hi) / 1e6,
                          generation_to_receipt_upper_ms=(row['receive_monotonic_ns'] - lo) / 1e6,
                          **configuration_at(actions, rpc, flow, node, lo, hi))
            records.append(record)
            lookup[index, node] = record
    records.sort(key=lambda r: (r['collector_row'], r['node']))
    tasks = load(directory / 'tasks.json')
    denominator = sum(t['weight'] * (t['end_us'] - t['start_us']) for t in tasks)
    task_rows = []
    visible = [r for r in records if r['fresh_per_flow']]
    for task in tasks:
        values = {}
        for observer, samples, time_low, time_high in (
            ('collector', records, 'received_ns', 'received_ns'),
            ('controller', visible, 'entered_lower_ns', 'entered_upper_ns')):
            for side, gen_side, at_side in (('lower', 'generation_upper_ns', time_low), ('upper', 'generation_lower_ns', time_high)):
                loss, unknown = task_integral(task, samples, origin, gen_side, at_side)
                values[observer + '_loss_' + side] = loss
                values[observer + '_unknown_' + side + '_ns'] = unknown
        proportion = task['weight'] * (task['end_us'] - task['start_us']) / denominator
        task_rows.append(dict(common, task_id=task['task_id'], node=task['object_id']['node'],
                              start_us=task['start_us'], end_us=task['end_us'], target_us=task['target_us'],
                              weight=task['weight'], denominator_weight_us=denominator, denominator_fraction=proportion,
                              **values,
                              penalty_lower=values['controller_loss_lower'] - values['collector_loss_upper'],
                              penalty_upper=values['controller_loss_upper'] - values['collector_loss_lower']))
    case_row = dict(common, report_records=len(records), visible_fresh_records=len(visible),
                    shared_cache_advances=sum(r['advances_shared_cache'] is True for r in records),
                    batch_latest_records=sum(r['batch_latest'] is True for r in records),
                    superseded_in_batch_records=sum(r['advances_shared_cache'] is True and not r['batch_latest'] for r in records),
                    tail_not_read_records=sum(r['status'] == 'not_read_in_cycles' for r in records),
                    rejected_records=sum(r['status'] in ('duplicate', 'invalid_report', 'old_record') for r in records),
                    task_count=len(tasks), denominator_weight_us=denominator,
                    window_feasibility=load(directory / 'window-costs.json')['window_feasibility'])
    for observer, source in (('collector', 'generation-bounds.json'), ('controller', 'controller-visible-bounds.json')):
        original = load(directory / source)
        for side in ('lower', 'upper'):
            value = sum(t['denominator_fraction'] * t[observer + '_loss_' + side] for t in task_rows)
            near(value, original['loss_' + side])
            case_row[observer + '_loss_' + side] = value
    case_row.update(penalty_lower=case_row['controller_loss_lower'] - case_row['collector_loss_upper'],
                    penalty_upper=case_row['controller_loss_upper'] - case_row['collector_loss_lower'])
    object_rows, batch_rows, traces = [], [], []
    for d, b in zip(decisions, batches):
        e = d['availability']
        batch_rows.append(dict(common, cycle_offset_us=d['scheduled_offset_us'], row_count=e['row_count'],
                               read_ms=(e['read_finished_ns'] - e['read_started_ns']) / 1e6,
                               ingest_ms=(e['available_ns'] - e['ingest_started_ns']) / 1e6,
                               read_started_ns=e['read_started_ns'], read_finished_ns=e['read_finished_ns'],
                               entered_lower_ns=e['ingest_started_ns'], entered_upper_ns=e['available_ns'],
                               cutoff_ns=d['cutoff_ns'], newly_accepted_records=len(b['accepted_records']),
                               cache_advances=len(b['cache_advances'])))
        for node in sorted({t['object_id']['node'] for t in tasks}):
            candidates = [r for r in records if r['node'] == node and r['batch_latest'] is True and
                          r['first_cycle_offset_us'] <= d['scheduled_offset_us']]
            selected = max(candidates, key=lambda r: r['first_cycle_offset_us']) if candidates else None
            lo = None if selected is None else (e['ingest_started_ns'] - selected['generation_upper_ns']) / 1e6
            hi = None if selected is None else (e['available_ns'] - selected['generation_lower_ns']) / 1e6
            object_rows.append(dict(common, cycle_offset_us=d['scheduled_offset_us'], node=node,
                                    sample_packet_position=None if selected is None else selected['packet_position'],
                                    sample_collector_row=None if selected is None else selected['collector_row'],
                                    refreshed_in_batch=any(n == node for _, n in b['cache_advances']),
                                    cache_age_lower_ms=lo, cache_age_upper_ms=hi,
                                    receipt_to_update_upper_ms=None if selected is None else
                                    (e['available_ns'] - selected['received_ns']) / 1e6))
        accepted_positions = sorted((lookup[index, node]['packet_position'], node) for index, node in b['accepted_records'])
        confirmed = {}
        for a in d['view']['confirmations']:
            if a['success']:
                key = (a['node'], a['flow']['session'], a['flow']['tag'], a['field'])
                confirmed[key] = a['value']
        traces.append({'cycle_offset_us': d['scheduled_offset_us'],
                       'notice_versions': [n['version'] for n in d['view']['notifications']],
                       'accepted_sample_positions': accepted_positions,
                       'paid_latest_counts': [(flow['session'], flow['tag'], value) for flow, value in
                                              [(r['flow'], r['count']) for r in d['view']['counts'][-2:]]],
                       'decision': {'config': d['selected']['config'], 'reason': d['selected']['reason']},
                       'confirmed_configuration': [list(k) + [v] for k, v in sorted(confirmed.items())]})
    notices = []
    for n in rows(directory / 'notices.jsonl'):
        first = next(d for d in decisions if any(v['version'] == n['message']['version'] for v in d['view']['notifications']))
        notices.append(dict(common, version=n['message']['version'], received_ns=n['received_ns'],
                            first_cycle_offset_us=first['scheduled_offset_us'], payload_bytes=n['payload_bytes']))
    case_row['initial_notice_first_cycle'] = bool(notices and notices[0]['first_cycle_offset_us'] == 0)
    sends = [dict(common, packet_id=s['packet_id'], packet_position=position[s['packet_id']],
                  planned_offset_ns=packets[position[s['packet_id']]]['offset_ns'],
                  send_before_ns=s['send_before_monotonic_ns'], send_after_ns=s['send_after_monotonic_ns'],
                  lateness_ms=s['schedule_lateness_ns'] / 1e6,
                  phase='warmup' if s['send_before_monotonic_ns'] < origin + 2000000000 else 'measurement')
             for s in rows(directory / 'send.jsonl')]
    return records, task_rows, case_row, object_rows, batch_rows, traces, notices, sends
