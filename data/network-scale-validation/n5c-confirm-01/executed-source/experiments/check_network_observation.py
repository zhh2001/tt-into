"""独立核对完整观察链、任务积分、配对分解和部署分歧。"""
import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path

from check_network_calibration import check_prefixes
from check_rolling_reference import clipped_loss
from observation_diagnostics import INPUT_SHA256, bind_input, load, rows, sha


def csv_rows(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def equal(a, b, message):
    if a != b:
        raise ValueError(message)


def close(a, b, message):
    if not math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError(message)


def direction(a, b, prefix):
    lo = float(a[prefix + '_loss_lower']) - float(b[prefix + '_loss_upper'])
    hi = float(a[prefix + '_loss_upper']) - float(b[prefix + '_loss_lower'])
    return {'lower': lo, 'upper': hi, 'direction': 'lower' if hi < 0 else 'higher' if lo > 0 else 'overlap'}


def check_index(anomalies, cases, batches, sends, repeats):
    equal(anomalies['budget_failures'], [c for c in cases if c['window_feasibility'] != 'within'], '失败索引不得省略或修改案例')
    equal(anomalies['largest_read_batches'], sorted(batches, key=lambda b: b['read_ms'], reverse=True)[:10], '读取异常索引不是完整排序')
    equal(anomalies['largest_send_lateness'], sorted(sends, key=lambda s: s['lateness_ms'], reverse=True)[:10], '发送异常索引不是完整排序')
    expected = sorted(repeats, key=lambda r: abs((r['collector_repeat_difference']['lower'] + r['collector_repeat_difference']['upper']) / 2), reverse=True)[:10]
    equal(anomalies['largest_collector_repeat_ranges'], expected, '重复范围索引不是完整排序')


def check_contributions(pair, dynamic_tasks, static_tasks):
    a, z = {r['task_id']: r for r in dynamic_tasks}, {r['task_id']: r for r in static_tasks}
    members = pair['task_contributions']
    equal({r['task_id'] for r in members}, set(a), '贡献分解必须保留全部任务')
    equal(len(members), len(a), '贡献任务不得重复')
    equal(set(a), set(z), '贡献配对任务一致')
    for r in members:
        t, s = a[r['task_id']], z[r['task_id']]
        equal(r['node'], int(t['node']), '贡献对象来源')
        fraction = float(t['denominator_fraction'])
        close(r['denominator_fraction'], fraction, '贡献权重来源')
        for observer in ('collector', 'controller'):
            expected = fraction * sum(float(t[observer + '_loss_' + side]) - float(s[observer + '_loss_' + side]) for side in ('lower', 'upper')) / 2
            close(r[observer + '_difference_midpoint_contribution'], expected, '逐任务贡献不得重新分配')
        expected = fraction * sum(float(t['penalty_' + side]) - float(s['penalty_' + side]) for side in ('lower', 'upper')) / 2
        close(r['penalty_difference_midpoint_contribution'], expected, '逐任务附加损失来源')


def check(analysis, diagnostic):
    manifest = load(diagnostic / 'manifest.json')
    equal(manifest['input_manifest_sha256'], INPUT_SHA256, '诊断没有绑定冻结比较')
    m = bind_input(analysis)
    equal(manifest['input_artifact_sha256'], m['artifact_sha256'], '完整输入摘要列表改变')
    for name, digest in manifest['artifact_sha256'].items():
        equal(sha(diagnostic / name), digest, '诊断产物摘要不符: ' + name)
    for name, digest in manifest['source_sha256'].items():
        equal(sha(diagnostic / 'executed-source' / name), digest, '执行源码摘要不符')
    equal((manifest['new_network_runs'], manifest['new_business_inputs']), (0, 0), '离线诊断边界改变')
    records = csv_rows(diagnostic / 'report-chains.csv')
    tasks = csv_rows(diagnostic / 'task-losses.csv')
    cases = csv_rows(diagnostic / 'case-diagnostics.csv')
    objects = csv_rows(diagnostic / 'object-batches.csv')
    batches = csv_rows(diagnostic / 'read-batches.csv')
    notices = csv_rows(diagnostic / 'notice-chains.csv')
    sends = csv_rows(diagnostic / 'send-timing.csv')
    traces = load(diagnostic / 'decision-traces.json')
    groups = {}
    for name, values in (('records', records), ('tasks', tasks), ('objects', objects), ('batches', batches), ('notices', notices), ('sends', sends)):
        groups[name] = defaultdict(list)
        for row in values: groups[name][row['name']].append(row)
    case_map = {c['name']: c for c in cases}
    equal(set(case_map), {c['name'] for c in m['cases']}, '案例分母不完整')
    equal(len(cases), 256, '案例重复')
    rpc = rows(analysis / 'rpc-operations.jsonl')
    checks = 0
    for case in m['cases']:
        name, origin = case['name'], case['origin_monotonic_ns']
        directory = analysis / name
        checks += check_prefixes(directory)
        for values in [groups[field][name] for field in groups] + [[case_map[name]]]:
            for row in values:
                for field in ('scenario', 'budget_level', 'method', 'seed', 'block'):
                    equal(row[field], str(case[field]), '诊断身份不得重新分类')
        physical = rows(directory / 'collector.jsonl')
        decisions = rows(directory / 'decisions.jsonl')
        raw = (directory / 'collector.jsonl').read_bytes()
        original_tasks = load(directory / 'tasks.json')
        generation = {(r['packet_id'], r['node']): r for r in load(directory / 'generation-bounds.json')['intervals']}
        packets = load(directory / 'packets.json')
        position = {p['packet_id']: index for index, p in enumerate(packets)}
        expected_keys = {(index, r['switch_id']) for index, row in enumerate(physical)
                         if row.get('ether_type') == 0x88b6 for r in row['records']}
        by_record = {(int(r['collector_row']), int(r['node'])): r for r in groups['records'][name]}
        equal(set(by_record), expected_keys, '逐样本完整分母错误')
        equal(len(by_record), len(groups['records'][name]), '样本链重复')
        prefixes, accepted, fresh_mapping = [], [], {}
        for d in decisions:
            e = d['availability']
            prefixes.append(raw[:e['prefix_bytes']].splitlines())
            for r in d['view']['reports'][e['reports_before']:e['reports_after']]:
                matches = [(index, wire) for index, wire in enumerate(physical)
                           if wire.get('ether_type') == 0x88b6 and
                           (wire['receive_monotonic_ns'] - origin) // 1000 == r['received_us'] and
                           (wire['session'], wire['flow_tag']) == (r['flow']['session'], r['flow']['tag']) and
                           all(any(q['switch_id'] == s['node'] and q['hop'] == s['hop'] and q['generated_us'] == s['generated_local_us']
                                   and q['queue_packets'] == s['queue_packets'] for q in wire['records']) for s in r['records'])]
                equal(len(matches), 1, '合法报告来源不唯一')
                index, wire = matches[0]
                for record in r['records']:
                    g = generation[wire['packet_id'], record['node']]
                    fresh_mapping[index, record['node']] = e
                    accepted.append((record['node'], g, e))
        expected_last = set()
        # 独立在每次批末选择实际view cache中的生成标记，不按诊断的latest标记选择。
        previous = {}
        for d in decisions:
            e = d['availability']
            for cached in d['view']['cache']:
                node = cached['object_id']['node']
                if previous.get(node) != cached['generated_local_us']:
                    candidates = [(index, record['switch_id']) for index, wire in enumerate(physical)
                                  if wire.get('ether_type') == 0x88b6 for record in wire['records']
                                  if record['switch_id'] == node and record['generated_us'] == cached['generated_local_us'] and
                                  (wire['receive_monotonic_ns'] - origin) // 1000 == cached['received_us']]
                    equal(len(candidates), 1, '批末缓存样本来源不唯一')
                    expected_last.add(candidates[0])
                previous[node] = cached['generated_local_us']
        shared_latest = {}
        for key, record in sorted(by_record.items()):
            index, node = key
            wire = physical[index]
            sample = next(x for x in wire['records'] if x['switch_id'] == node)
            g = generation[wire['packet_id'], node]
            equal(int(record['packet_id']), wire['packet_id'], '离线样本身份')
            equal(int(record['packet_position']), position[wire['packet_id']], '离线样本位置')
            equal(int(record['local_generated_us']), sample['generated_us'], '生成标记来源')
            equal(int(record['received_ns']), wire['receive_monotonic_ns'], '实收取时来源')
            for side in ('lower', 'upper'):
                equal(int(record['generation_' + side + '_ns']), g['generation_' + side + '_ns'], '生成条件界来源')
            first = next((d for d, prefix in zip(decisions, prefixes) if index < len(prefix)), None)
            fresh = key in fresh_mapping
            equal(record['fresh_per_flow'], str(fresh) if first else '', '新报告进入视图来源')
            if first is None:
                equal(record['status'], 'not_read_in_cycles', '未读尾段状态')
                for field in ('read_finished_ns', 'entered_lower_ns', 'entered_upper_ns', 'first_cutoff_ns', 'first_cycle_offset_us', 'batch_latest'):
                    equal(record[field], '', '尾段没有虚构进入周期')
            else:
                e = first['availability']
                for field, source in (('read_finished_ns', 'read_finished_ns'), ('entered_lower_ns', 'ingest_started_ns'),
                                      ('entered_upper_ns', 'available_ns'), ('first_cutoff_ns', 'cutoff_ns')):
                    equal(int(record[field]), e[source], '进入状态时间来源')
                equal(int(record['first_cycle_offset_us']), first['scheduled_offset_us'], '首次周期来源')
                close(record['receipt_to_entry_lower_ms'], (e['ingest_started_ns'] - wire['receive_monotonic_ns']) / 1e6, '可用延迟下界')
                close(record['receipt_to_entry_upper_ms'], (e['available_ns'] - wire['receive_monotonic_ns']) / 1e6, '可用延迟上界')
                equal(record['batch_latest'], str(key in expected_last), '批末缓存样本来源')
                flow = wire['session'], wire['flow_tag']
                old = shared_latest.get(node)
                advance = fresh and (old is None or 0 < (sample['generated_us'] - old) % 2**48 < 2**47)
                equal(record['advances_shared_cache'], str(advance), '共享缓存推进来源')
                if advance: shared_latest[node] = sample['generated_us']
            actions = [a for a in rows(directory / 'actions.jsonl') if a['node'] == node and
                       (a['flow']['session'], a['flow']['tag']) == (wire['session'], wire['flow_tag'])]
            confirmed = [a for a in actions if a['success'] and a['confirmation_available_ns'] <= g['generation_lower_ns']]
            for field, output_field in (('threshold_us', 'confirmed_threshold_us'), ('enabled', 'confirmed_enabled')):
                values = [a['value'] for a in confirmed if a['field'] == field]
                expected = '' if not values else str(int(values[-1]))
                equal(record[output_field], expected, '实际确认字段来源')
            latest_confirmation = max((a['confirmation_available_ns'] for a in confirmed), default=None)
            equal(record['last_confirmation_ns'], '' if latest_confirmation is None else str(latest_confirmation), '最后确认取时来源')
            overlap = sum(a['success'] and rpc[a['rpc_index']]['before_monotonic_ns'] <= g['generation_upper_ns'] and
                          rpc[a['rpc_index']]['after_monotonic_ns'] >= g['generation_lower_ns'] for a in actions)
            unconfirmed = sum(a['success'] and rpc[a['rpc_index']]['before_monotonic_ns'] <= g['generation_upper_ns'] and
                              a['confirmation_available_ns'] > g['generation_lower_ns'] for a in actions)
            equal(int(record['configuration_interval_overlaps']), overlap, '部分生效区间来源')
            equal(int(record['configuration_not_yet_confirmed']), unconfirmed, '尚未确认的字段来源')
            checks += 20
        task_map = {t['task_id']: t for t in groups['tasks'][name]}
        equal(set(task_map), {t['task_id'] for t in original_tasks}, '全部活跃任务分母')
        equal(len(task_map), len(groups['tasks'][name]), '任务重复')
        denominator = sum(t['weight'] * (t['end_us'] - t['start_us']) for t in original_tasks)
        for task in original_tasks:
            row = task_map[task['task_id']]
            close(row['denominator_fraction'], task['weight'] * (task['end_us'] - task['start_us']) / denominator, '任务权重分母')
            for observer in ('collector', 'controller'):
                for side in ('lower', 'upper'):
                    generated = 'generation_upper_ns' if side == 'lower' else 'generation_lower_ns'
                    samples = []
                    if observer == 'collector':
                        for wire in physical:
                            if wire.get('ether_type') != 0x88b6: continue
                            for r in wire['records']:
                                g = generation[wire['packet_id'], r['switch_id']]
                                samples.append({'object_id': {'node': r['switch_id'], 'port': 2, 'queue': 0},
                                                'generated_us': (g[generated] - origin) / 1000,
                                                'received_us': (wire['receive_monotonic_ns'] - origin) / 1000})
                    else:
                        for node, g, e in accepted:
                            samples.append({'object_id': {'node': node, 'port': 2, 'queue': 0},
                                            'generated_us': (g[generated] - origin) / 1000,
                                            'received_us': (e['ingest_started_ns' if side == 'lower' else 'available_ns'] - origin) / 1000})
                    loss = clipped_loss([task], samples, 4)[0]
                    close(row[observer + '_loss_' + side], loss, '独立分段积分不符')
                    available = [s['received_us'] for s in samples if s['object_id'] == task['object_id']]
                    unknown = min(task['end_us'], max(task['start_us'], min(available, default=task['end_us']))) - task['start_us']
                    close(row[observer + '_unknown_' + side + '_ns'], unknown * 1000, '未知状态时长不符')
                    checks += 2
            close(row['penalty_lower'], float(row['controller_loss_lower']) - float(row['collector_loss_upper']), '观察附加损失下界')
            close(row['penalty_upper'], float(row['controller_loss_upper']) - float(row['collector_loss_lower']), '观察附加损失上界')
        case_row = case_map[name]
        for observer, file in (('collector', 'generation-bounds.json'), ('controller', 'controller-visible-bounds.json')):
            original = load(directory / file)
            for side in ('lower', 'upper'):
                value = sum(float(t['denominator_fraction']) * float(t[observer + '_loss_' + side]) for t in task_map.values())
                close(case_row[observer + '_loss_' + side], value, '完整案例权重积分')
                close(value, original['loss_' + side], '原年龄评价未改变')
        expected_counts = {'report_records': len(by_record), 'visible_fresh_records': len(fresh_mapping),
                           'shared_cache_advances': sum(r['advances_shared_cache'] == 'True' for r in by_record.values()),
                           'batch_latest_records': len(expected_last),
                           'superseded_in_batch_records': sum(r['advances_shared_cache'] == 'True' and r['batch_latest'] != 'True' for r in by_record.values()),
                           'tail_not_read_records': sum(r['status'] == 'not_read_in_cycles' for r in by_record.values()),
                           'rejected_records': sum(r['status'] in ('duplicate', 'invalid_report', 'old_record') for r in by_record.values()),
                           'task_count': len(original_tasks), 'denominator_weight_us': denominator}
        for field, value in expected_counts.items(): equal(int(case_row[field]), value, '案例计数分母错误')
        for field in ('penalty_lower', 'penalty_upper'):
            side = field.removeprefix('penalty_')
            close(case_row[field], float(case_row['controller_loss_' + side]) - float(case_row['collector_loss_' + ('upper' if side == 'lower' else 'lower')]), '案例附加损失')
        equal(case_row['window_feasibility'], load(directory / 'window-costs.json')['window_feasibility'], '失败预算资格保持')
        for d, batch, trace in zip(decisions, groups['batches'][name], traces[name]):
            e = d['availability']
            close(batch['read_ms'], (e['read_finished_ns'] - e['read_started_ns']) / 1e6, '真实读取时长')
            close(batch['ingest_ms'], (e['available_ns'] - e['ingest_started_ns']) / 1e6, '真实缓存更新时长')
            equal(trace['decision'], {k: d['selected'][k] for k in ('config', 'reason')}, '选择决策来源')
            equal(trace['notice_versions'], [n['version'] for n in d['view']['notifications']], '需求版本来源')
            expected_positions = sorted([int(r['packet_position']), int(r['node'])] for r in by_record.values()
                                        if r['fresh_per_flow'] == 'True' and int(r['first_cycle_offset_us']) == d['scheduled_offset_us'])
            equal(trace['accepted_sample_positions'], expected_positions, '合法样本业务位置来源')
            equal(trace['paid_latest_counts'], [[r['flow']['session'], r['flow']['tag'], r['count']] for r in d['view']['counts'][-2:]], '付费计数来源')
            conf = {}
            for r in d['view']['confirmations']:
                if r['success']: conf[r['node'], r['flow']['session'], r['flow']['tag'], r['field']] = r['value']
            equal(trace['confirmed_configuration'], [list(k) + [v] for k, v in sorted(conf.items())], '确认配置来源')
            cache = {r['object_id']['node']: r for r in d['view']['cache']}
            object_rows = [r for r in groups['objects'][name] if int(r['cycle_offset_us']) == d['scheduled_offset_us']]
            equal({int(r['node']) for r in object_rows}, {t['object_id']['node'] for t in original_tasks}, '全部对象批次')
            for r in object_rows:
                node = int(r['node'])
                if node not in cache:
                    equal(r['cache_age_lower_ms'], '', '未知缓存年龄不得写零')
                    equal(r['cache_age_upper_ms'], '', '未知缓存年龄不得写零')
                else:
                    index = next(index for index, wire in enumerate(physical) if wire.get('ether_type') == 0x88b6 and
                                 any(s['switch_id'] == node and s['generated_us'] == cache[node]['generated_local_us'] for s in wire['records']) and
                                 (wire['receive_monotonic_ns'] - origin) // 1000 == cache[node]['received_us'])
                    wire, g = physical[index], generation[physical[index]['packet_id'], node]
                    equal(int(r['sample_collector_row']), index, '批末缓存索引')
                    close(r['cache_age_lower_ms'], (e['ingest_started_ns'] - g['generation_upper_ns']) / 1e6, '批末缓存年龄下界')
                    close(r['cache_age_upper_ms'], (e['available_ns'] - g['generation_lower_ns']) / 1e6, '批末缓存年龄上界')
                    close(r['receipt_to_update_upper_ms'], (e['available_ns'] - wire['receive_monotonic_ns']) / 1e6, '缓存延迟来源')
                checks += 4
            checks += 7
        equal(len(groups['batches'][name]), len(decisions), '完整批次数量')
        equal(len(groups['objects'][name]), len(decisions) * len({t['object_id']['node'] for t in original_tasks}), '完整对象批次数量')
        equal(len(traces[name]), len(decisions), '完整决策轨迹')
        for actual, recorded in zip(rows(directory / 'notices.jsonl'), groups['notices'][name]):
            equal(int(recorded['received_ns']), actual['received_ns'], '实际通知可用')
            first = next(d for d in decisions if any(n['version'] == actual['message']['version'] for n in d['view']['notifications']))
            equal(int(recorded['first_cycle_offset_us']), first['scheduled_offset_us'], '首次通知周期')
            equal(int(recorded['payload_bytes']), actual['payload_bytes'], '通知实际字节')
            checks += 3
        equal(len(groups['notices'][name]), len(rows(directory / 'notices.jsonl')), '实际通知完整分母')
        first_cycle_notice = bool(groups['notices'][name] and int(groups['notices'][name][0]['first_cycle_offset_us']) == 0)
        equal(case_row['initial_notice_first_cycle'], str(first_cycle_notice), '实际首通知分组')
        for actual, recorded in zip(rows(directory / 'send.jsonl'), groups['sends'][name]):
            equal(int(recorded['send_before_ns']), actual['send_before_monotonic_ns'], '实际发送时序')
            close(recorded['lateness_ms'], actual['schedule_lateness_ns'] / 1e6, '实际发送迟到')
            checks += 2
        equal(len(groups['sends'][name]), len(packets), '所有业务发送保留')
        checks += 25
    paired = load(diagnostic / 'paired-observers.json')
    original_pairs = {(p['dynamic_case'], p['static_case']): p for p in load(analysis / 'paired-cases.json')}
    equal(len(paired), 224, '配对分母错误')
    equal({(p['dynamic_case'], p['static_case']) for p in paired}, set(original_pairs), '完整配对身份')
    for p in paired:
        a, z = case_map[p['dynamic_case']], case_map[p['static_case']]
        original = original_pairs[p['dynamic_case'], p['static_case']]
        equal(p['both_within'], original['both_within'], '配对预算资格保持')
        check_contributions(p, groups['tasks'][p['dynamic_case']], groups['tasks'][p['static_case']])
        for observer in ('collector', 'controller'):
            expected = direction(a, z, observer)
            equal(p[observer + '_difference']['direction'], expected['direction'], '观察位置方向错误')
            for side in ('lower', 'upper'):
                close(p[observer + '_difference'][side], expected[side], '配对条件差值来源')
                close(expected[side], original[observer + '_difference'][side], '原配对差值保持')
            close(sum(t[observer + '_difference_midpoint_contribution'] for t in p['task_contributions']),
                  (expected['lower'] + expected['upper']) / 2, '逐任务贡献分解保持')
        close(p['penalty_difference']['lower'], float(a['penalty_lower']) - float(z['penalty_upper']), '附加损失差下界')
        close(p['penalty_difference']['upper'], float(a['penalty_upper']) - float(z['penalty_lower']), '附加损失差上界')
        for t in p['task_contributions']:
            close(t['collector_difference_midpoint_contribution'] + t['penalty_difference_midpoint_contribution'],
                  t['controller_difference_midpoint_contribution'], '观察链中点分解恒等式')
        checks += 16
    repeats = load(diagnostic / 'deployment-divergences.json')
    equal(len(repeats), 128, '部署重复分母错误')
    identities = {(c['scenario'], c['budget_level'], int(c['seed']), c['method']) for c in cases}
    equal({(r['scenario'], r['budget_level'], r['seed'], r['method']) for r in repeats}, identities, '重复身份保持')
    for r in repeats:
        a, b = [case_map[name] for name in r['cases']]
        equal([int(a['block']), int(b['block'])], [1, 2], '部署块身份')
        equal(r['initial_notice_group_mismatch'], a['initial_notice_first_cycle'] != b['initial_notice_first_cycle'], '首通知分组')
        for field, first in r['first_divergences'].items():
            expected = next(({'cycle_offset_us': x['cycle_offset_us'], 'block1': x[field], 'block2': y[field]}
                            for x, y in zip(traces[a['name']], traces[b['name']]) if x[field] != y[field]), None)
            equal(first, expected, '第一分歧完整来源')
        signatures = [{(int(v['packet_position']), int(v['node'])) for v in groups['records'][name]} for name in r['cases']]
        symmetric = sorted(signatures[0] ^ signatures[1])
        equal(r['sampling_position_symmetric_difference'], len(symmetric), '全部采样位置差异')
        equal(r['first_different_sampling_position'], list(symmetric[0]) if symmetric else None, '首个采样位置差异')
        checks += 9
    anomalies = load(diagnostic / 'anomaly-index.json')
    equal({c['name'] for c in anomalies['budget_failures']}, {c['name'] for c in cases if c['window_feasibility'] != 'within'}, '全部失败预算保留')
    # CSV只有字符串，按索引中的原始字段类型恢复后独立核对完整排序。
    def typed(values, example):
        def convert(value, reference):
            if reference is None: return None if value == '' else float(value)
            if isinstance(reference, bool): return value == 'True'
            if isinstance(reference, int): return int(value)
            if isinstance(reference, float): return float(value)
            return value
        return [{k: convert(v, example[k]) for k, v in row.items()} for row in values]
    check_index(anomalies, typed(cases, anomalies['budget_failures'][0]),
                typed(batches, anomalies['largest_read_batches'][0]), typed(sends, anomalies['largest_send_lateness'][0]), repeats)
    aggregated = csv_rows(diagnostic / 'method-diagnostics.csv')
    aggregate_keys = {(c['scenario'], c['budget_level'], c['method']) for c in cases}
    equal({tuple(a[k] for k in ('scenario', 'budget_level', 'method')) for a in aggregated}, aggregate_keys, '完整方法汇总')
    equal(len(aggregated), len(aggregate_keys), '汇总单元不得重复')
    for a in aggregated:
        members = [c for c in cases if all(c[k] == a[k] for k in ('scenario', 'budget_level', 'method'))]
        equal(int(a['case_count']), len(members), '汇总案例分母')
        equal(int(a['business_count']), len({c['seed'] for c in members}), '独立业务分母')
        equal(int(a['notice_first_cycle_count']), sum(c['initial_notice_first_cycle'] == 'True' for c in members), '首周期通知汇总')
        for field in a:
            if field.endswith('_mean'):
                source = field.removesuffix('_mean')
                close(a[field], sum(float(c[source]) for c in members) / len(members), '方法均值来源')
                checks += 1
    findings = load(diagnostic / 'findings.json')
    expected_findings = {'cases': len(cases), 'report_records': len(records), 'task_intervals': len(tasks),
                         'batch_objects': len(objects), 'decision_batches': len(batches), 'paired_cases': len(paired),
                         'repeat_groups': len(repeats), 'budget_failures': len(anomalies['budget_failures']),
                         'initial_notice_group_mismatches': sum(r['initial_notice_group_mismatch'] for r in repeats),
                         'repeat_decision_divergences': sum(r['first_divergences']['decision'] is not None for r in repeats),
                         'same_initial_notice_but_different_decisions': sum(not r['initial_notice_group_mismatch'] and r['first_divergences']['decision'] is not None for r in repeats)}
    for field in ('visible_fresh_records', 'shared_cache_advances', 'batch_latest_records', 'superseded_in_batch_records', 'tail_not_read_records', 'rejected_records'):
        expected_findings[field] = sum(int(c[field]) for c in cases)
    for field, value in expected_findings.items(): equal(findings[field], value, '发现结果分母错误')
    primary = [p for p in paired if p['scenario'] == 'shared_priority' and p['method'] == 'phase_guard']
    for observer in ('collector', 'controller'):
        equal(findings['priority_phase_guard_' + observer + '_directions'], dict(Counter(p[observer + '_difference']['direction'] for p in primary)), '主要方向分母')
    return {'status': 'passed', 'checks': checks, 'cases': len(cases), 'records': len(records),
            'tasks': len(tasks), 'paired_cases': len(paired), 'repeat_groups': len(repeats),
            'input_manifest_sha256': sha(analysis / 'manifest.json'),
            'diagnostic_manifest_sha256': sha(diagnostic / 'manifest.json')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--diagnostic', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = check(args.analysis, args.diagnostic)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        with args.output.open('x') as stream: stream.write(encoded)
    print(encoded)
