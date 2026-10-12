#!/usr/bin/env python3
"""独立核对实际选择、丢失报告费用、因果决策与连续年龄区间。"""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
from itertools import product
import json
from pathlib import Path
import shutil
import sys

from scapy.utils import RawPcapReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from collector.strong_codec import decode_frame, STATE_TYPE
from orchestration.metrics import age_metrics
from orchestration.model import Task


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_clock_decisions(directory, case):
    reports = {r['packet_id']: r for r in rows(directory / 'collector.jsonl') if r.get('ether_type') == STATE_TYPE}
    sends = {r['packet_id']: r for r in rows(directory / 'send.jsonl')}
    before = set()
    previous = {r['node']: r['flow'] for r in case['cold_decision']['assignment']}
    queue = case['cold_decision']['queue_after']
    count = 0
    for row in rows(directory / 'decisions.jsonl'):
        cutoff, ids = row['cutoff_monotonic_ns'], set(row['observed_packet_ids'])
        if not before <= ids or not ids <= set(reports) or len(ids) != len(row['observed_packet_ids']):
            raise RuntimeError('消费报告集合不合法')
        if not row['scheduled_monotonic_ns'] <= row['began_monotonic_ns'] <= cutoff <= row['finished_monotonic_ns']:
            raise RuntimeError('决策执行因果顺序错误')
        expected = []
        for node in (1, 2, 3):
            bounds, latest = [], None
            for pid in ids:
                report, sent = reports[pid], sends[pid]
                if report['receive_monotonic_ns'] > cutoff or sent['send_before_monotonic_ns'] > report['receive_monotonic_ns']:
                    raise RuntimeError('决策看到了未收到报告或错误时间界')
                for item in report['records']:
                    if item['switch_id'] == node:
                        g = item['generated_us']
                        bounds.append((sent['send_before_monotonic_ns'] - g * 1000, report['receive_monotonic_ns'] - g * 1000))
                        latest = max(g, latest if latest is not None else g)
            if not bounds:
                value = {'node': node, 'latest_generated_us': None, 'age_lower_us': None,
                         'age_upper_us': None, 'offset_interval_ns': None}
            else:
                lo, hi = max(b[0] for b in bounds), min(b[1] for b in bounds)
                if lo > hi:
                    raise RuntimeError('决策固定偏移不兼容')
                value = {'node': node, 'latest_generated_us': latest,
                         'age_lower_us': max(0, (cutoff - latest * 1000 - hi) / 1000),
                         'age_upper_us': max(0, (cutoff - latest * 1000 - lo) / 1000), 'offset_interval_ns': [lo, hi]}
            expected.append(value)
        if expected != row['observations'] or row['writes'] or row['extra_network_polling_calls']:
            raise RuntimeError('观察快照或额外控制调用不符')
        action, family, params = row['action'], case['family'], case['parameters']
        if action['thresholds'] != case['target'] or action['queue_before'] != queue:
            raise RuntimeError('在线目标或虚拟队列不连续')
        if family != 'tt':
            rank = {f: i + 1 for i, f in enumerate(params['flow_order'])}
            best = None
            for assignment in product((0, 1), (0, 2), (0, 1, 2, 3)):
                if any(assignment.count(f) > case['capacity'] for f in (1, 2, 3)):
                    continue
                delta = 12 * sum(abs(int(assignment[n - 1] == f) - int(previous.get(n) == f))
                                 for n in (1, 2, 3) for f in (1, 2, 3))
                if family == 'dinto':
                    gains = [2, 2, 1]
                    if params['gain'] == 'age':
                        gains = [w * (1 + (3 if r['age_upper_us'] is None else min(3, r['age_upper_us'] / d)))
                                 for w, r, d in zip(gains, expected, (220000, 220000, 260000))]
                    objective = queue * delta - params['tradeoff'] * sum(w for w, f in zip(gains, assignment) if f)
                else:
                    ahead = params['lookahead_us'] if family == 'intopp_ahead' else 0
                    objective = -sum(w for w, r, d, f in zip((2, 2, 1), expected, (220000, 220000, 260000), assignment)
                                     if f and (r['age_upper_us'] is None or r['age_upper_us'] + ahead > d))
                candidate = (objective, delta, tuple(rank.get(f, 0) for f in assignment), assignment)
                if best is None or candidate[:3] < best[:3]:
                    best = candidate
            chosen = {r['node']: r['flow'] for r in action['assignment']}
            if tuple(chosen.get(n, 0) for n in (1, 2, 3)) != best[3] or action['variation_bytes'] != best[1]:
                raise RuntimeError('实收状态下的分配不是原目标最优解')
            if family == 'dinto':
                queue = max(0, queue + best[1] - params['average_change_bytes'])
                score = best[0]
            else:
                score = -best[0]
            if action['queue_after'] != queue or abs(action['objective'] - score) > 1e-9:
                raise RuntimeError('原目标或队列递推不符')
            previous = chosen
        before = ids
        count += 1
    if count != 31:
        raise RuntimeError('时隙数不足')
    return {'checked_decisions': count, 'passed': True,
            'maximum_slot_lateness_us': max((r['began_monotonic_ns'] - r['scheduled_monotonic_ns']) / 1000
                                            for r in rows(directory / 'decisions.jsonl')),
            'maximum_observe_and_solve_us': max((r['finished_monotonic_ns'] - r['began_monotonic_ns']) / 1000
                                               for r in rows(directory / 'decisions.jsonl'))}


def analyze_age(directory, case, emitted):
    sends = {r['packet_id']: r for r in rows(directory / 'send.jsonl')}
    business = {r['packet_id']: r for r in rows(directory / 'destination.jsonl') if r.get('kind') == 'frame' and r.get('src') == '10.0.0.1'}
    received = [r for r in rows(directory / 'collector.jsonl') if r.get('ether_type') == STATE_TYPE]
    offsets = {n: [] for n in (1, 2, 3)}
    for pid, report in emitted.items():
        lo, hi = sends[pid]['send_before_monotonic_ns'], business[pid]['receive_monotonic_ns']
        if lo > hi:
            raise RuntimeError('业务因果时间倒置')
        for item in report['records']:
            offsets[item['switch_id']].append((lo - item['generated_us'] * 1000, hi - item['generated_us'] * 1000))
    intervals = {n: (max(v[0] for v in values), min(v[1] for v in values)) for n, values in offsets.items()}
    if any(lo > hi for lo, hi in intervals.values()):
        raise RuntimeError('离线固定时钟偏移区间为空')
    origin = case['origin_monotonic_ns']
    samples = {n: {'lower_generation': [], 'upper_generation': []} for n in (1, 2, 3)}
    delay_intervals = []
    for report in received:
        pid, arrived = report['packet_id'], report['receive_monotonic_ns']
        if arrived < sends[pid]['send_before_monotonic_ns']:
            raise RuntimeError('收报先于业务发送')
        for item in report['records']:
            node, generated = item['switch_id'], item['generated_us'] * 1000
            lo, hi = intervals[node]
            if generated + lo > arrived:
                raise RuntimeError('收报先于生成下界')
            lower = max(generated + lo, sends[pid]['send_before_monotonic_ns'])
            upper = min(generated + hi, business[pid]['receive_monotonic_ns'], arrived)
            if lower > upper:
                raise RuntimeError('报告生成区间为空')
            samples[node]['lower_generation'].append(((lower - origin) / 1000, (arrived - origin) / 1000))
            samples[node]['upper_generation'].append(((upper - origin) / 1000, (arrived - origin) / 1000))
            delay_intervals.append(((arrived - upper) / 1000, (arrived - lower) / 1000))
    tasks = []
    for node, deadline in enumerate((220000, 220000, 260000), 1):
        lower = age_metrics(Task(node, deadline), samples[node]['upper_generation'], 800000, 3200000)
        upper = age_metrics(Task(node, deadline), samples[node]['lower_generation'], 800000, 3200000)
        tasks.append({'node': node, 'deadline_us': deadline, 'violation_lower': lower['violation_fraction'],
                      'violation_upper': upper['violation_fraction'], 'p95_age_lower_us': lower['p95_age_us'],
                      'p95_age_upper_us': upper['p95_age_us'], 'unknown_fraction': upper['unknown_fraction'],
                      'fulfilled_by_upper_age': upper['fulfilled']})
    return {'tasks': tasks, 'fulfilled_weight_by_upper_age': sum(w for w, r in zip((2, 2, 1), tasks) if r['fulfilled_by_upper_age']),
            'clock_offset_intervals_ns': {str(n): list(v) for n, v in intervals.items()},
            'maximum_generation_interval_width_us': max((hi - lo) / 1000 for lo, hi in intervals.values()),
            'record_delivery_delay_bounds_us': {'minimum_lower': min(v[0] for v in delay_intervals),
                                              'maximum_upper': max(v[1] for v in delay_intervals)},
            'assumptions': '单宿主单调时钟，生成早于对应恢复业务接收，单节点恒定偏移；非精确年龄真值',
            'clock_calibration': '离线用发送前及业务接收界收紧区间；全部实际导出含丢失报告只校准时钟，不作为已收到状态',
            'availability': '实际 collector recvmsg 完成，窗口后报告保留但不提前使用',
            'maximum_send_lateness_us': max(r['schedule_lateness_ns'] for r in sends.values()) / 1000}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.output.resolve()
    if out.exists() or run in out.parents or out in run.parents:
        raise RuntimeError('派生目录须独立且不存在')
    summary = json.loads((run / 'manifest.json').read_text())
    if summary['status'] != 'passed':
        raise RuntimeError('原始执行未通过')
    for name, digest in summary['source_sha256'].items():
        if sha(run / 'executed-source' / name) != digest:
            raise RuntimeError('实际源码归档摘要不符 ' + name)
    for name, digest in summary['input_sha256'].items():
        if sha(run / name) != digest:
            raise RuntimeError('冻结输入摘要不符 ' + name)
    mapping, costs, case_map = {}, {}, {r['name']: r for r in summary['cases']}
    frozen = json.loads((run / 'frozen-parameters.json').read_text())
    source_packets = json.loads((run / 'source-workload.json').read_text())['packets']
    for case in summary['cases']:
        name = case['name']
        selected = next(r for r in frozen if r['family'] == case['family'] and r['capacity'] == case['capacity'])
        if case['parameters'] != selected['parameters']:
            raise RuntimeError('配置未沿用冻结参数')
        expected_target = [{'node': n, 'flow': f, 'threshold_us': (1 if (n, f) in
                           ((1, 1), (2, 2), (3, 3 if case['capacity'] == 1 else 2)) else None)}
                           for n, f in ((1, 1), (2, 2), (3, 1), (3, 2), (3, 3))]
        if case['target'] != expected_target:
            raise RuntimeError('最终共同分配未正确转为寄存器目标')
        inputs = json.loads((run / name / 'packets.json').read_text())
        if len(inputs) != len(source_packets) or any(p['flow'] != q['flow'] or p['path'] != q['path'] or
                p['offset_ns'] != (q['gate_us'][0] + 800000) * 1000 for p, q in zip(inputs, source_packets)):
            raise RuntimeError('业务轨迹没有沿用冻结源端输入')
        for packet in inputs:
            if packet['packet_id'] in mapping:
                raise RuntimeError('全程输入标识重复')
            mapping[packet['packet_id']] = name, packet
        costs[name] = {'inband_per_link_bytes': [0, 0, 0], 'export_frame_bytes': 0,
                       'export_padded_bytes': 0, 'frames_by_node_port': Counter(), 'emitted': {}, 'wire': []}
    seen = set()
    for path in sorted(run.glob('node-*/*_out.pcap')):
        node = int(path.parent.name.split('-')[1])
        port = 2 if path.name.endswith('-eth2_out.pcap') else 3 if node == 4 and path.name.endswith('-eth3_out.pcap') else None
        if port is None or not path.stat().st_size:
            continue
        with closing(RawPcapReader(str(path))) as reader:
            for frame, _ in reader:
                row = decode_frame(frame)
                if not row or row['src'] != '10.0.0.1' or row['dst'] != '10.0.0.2':
                    continue
                pid = row['packet_id']
                if pid not in mapping or (pid, node, port) in seen:
                    raise RuntimeError('未知或重复链路包')
                seen.add((pid, node, port))
                name, packet = mapping[pid]
                cost, case = costs[name], case_map[name]
                target = {(r['node'], r['flow']): r['threshold_us'] for r in case['target']}
                selected = [n for n in reversed(packet['path']) if target[n, packet['flow']] is not None]
                if not row['ip_checksum_ok'] or row['ip_length'] > 1500:
                    raise RuntimeError('链路校验和或 MTU 错误')
                if node < 4:
                    if node not in packet['path']:
                        raise RuntimeError('物理路径资格错误')
                    prefix = packet['path'][:packet['path'].index(node) + 1]
                    wanted = [n for n in selected if n in prefix]
                    extra = row['ip_length'] - packet['ip_length']
                    if row['ether_type'] != STATE_TYPE or row['count'] > case['capacity'] or extra != 4 + 12 * len(wanted) or [r['switch_id'] for r in row['records']] != wanted:
                        raise RuntimeError('真实前缀传播或项数不符')
                    cost['inband_per_link_bytes'][node - 1] += extra
                elif port == 2:
                    extra = row['ip_length'] - packet['ip_length']
                    if extra or row['ether_type'] != 0x0800:
                        raise RuntimeError('业务未恢复')
                else:
                    extra = len(frame)
                    if row['ether_type'] != STATE_TYPE or not selected or row['count'] != len(selected) or row['ip_length'] != 24 + 12 * len(selected) or [r['switch_id'] for r in row['records']] != selected:
                        raise RuntimeError('sink 导出格式或选择错误')
                    cost['emitted'][pid] = row
                    cost['export_frame_bytes'] += len(frame)
                    cost['export_padded_bytes'] += max(60, len(frame))
                cost['frames_by_node_port'][f'{node}:{port}'] += 1
                cost['wire'].append({'packet_id': pid, 'node': node, 'port': port, 'frame_bytes': len(frame),
                                     'ip_bytes': row['ip_length'], 'extra_bytes': extra, 'records': row.get('records', [])})
    operations = rows(run / 'rpc-operations.jsonl')
    for name, case in case_map.items():
        directory, cost = run / name, costs[name]
        originals = [p for n, p in mapping.values() if n == name]
        target = {(r['node'], r['flow']): r['threshold_us'] for r in case['target']}
        expected_emitted = {p['packet_id'] for p in originals if any(target[n, p['flow']] is not None for n in p['path'])}
        expected_counts = {f'{n}:2': sum(n in p['path'] for p in originals) for n in (1, 2, 3)}
        expected_counts.update({'4:2': len(originals), '4:3': len(expected_emitted)})
        if set(cost['emitted']) != expected_emitted or dict(cost['frames_by_node_port']) != expected_counts:
            raise RuntimeError('真实业务路径或 sink 导出完整性错误')
        reports = {r['packet_id']: r for r in rows(directory / 'collector.jsonl') if r.get('ether_type') == STATE_TYPE}
        if not set(reports) <= expected_emitted:
            raise RuntimeError('收到未导出的报告')
        for pid, report in reports.items():
            if report['records'] != cost['emitted'][pid]['records']:
                raise RuntimeError('导出与接收的完整记录不同')
        missing = sorted(expected_emitted - set(reports))
        before_tc = json.loads((directory / 'netem-before.json').read_text())['statistics'][0]
        after_tc = json.loads((directory / 'netem-after.json').read_text())[0]
        drops = after_tc['drops'] - before_tc['drops']
        if after_tc['qlen'] or drops < len(missing) or case['scenario'] != 'loss' and missing:
            raise RuntimeError('导出链路丢失、排空或 qdisc 计数不符')
        case['export_delivery'] = {'emitted_reports': len(expected_emitted), 'received_reports': len(reports),
            'missing_packet_ids': missing, 'missing_reports': len(missing), 'netem_drop_counter_delta': drops,
            'netem_scope': 'qdisc 还可含背景帧，不能把总 drops 全部归给遥测报告',
            'emitted_record_counts': [sum(r['switch_id'] == n for p in cost['emitted'].values() for r in p['records']) for n in (1, 2, 3)]}
        if summary['diagnostics']:
            observed = {(r['packet_id'], r['node']): r for r in rows(directory / 'observations.jsonl')}
            for pid, report in cost['emitted'].items():
                for item in report['records']:
                    tr = observed[pid, item['switch_id']]
                    if item['generated_us'] != tr['trace_generated'] or item['queue_packets'] != tr['trace_value']:
                        raise RuntimeError('含丢失报告的生成状态与诊断不符')
        case['decision_checks'] = check_clock_decisions(directory, case)
        case['age'] = analyze_age(directory, case, cost['emitted'])
        sends = rows(directory / 'send.jsonl')
        first_send = min(r['send_before_monotonic_ns'] for r in sends)
        if case['activated_monotonic_ns'] >= first_send:
            raise RuntimeError('初次配置晚于业务')
        if any(r['before_monotonic_ns'] >= first_send and
               r['before_monotonic_ns'] < case['origin_monotonic_ns'] + 3200000000 for r in operations):
            raise RuntimeError('测量过程中出现未声明的控制网络调用')
        api = {}
        for kind, field in (('installation', 'installation_rpc_indices'), ('initial_activation', 'activation_rpc_indices')):
            begin, end = case[field]
            calls = operations[begin:end]
            if kind == 'initial_activation' and (len(calls) != 3 or any(r['kind'] != kind or r['after_monotonic_ns'] >= first_send or r['arguments'][0] != 'TTEgress.max_count' for r in calls)):
                raise RuntimeError('容量启用费用或完成边界错误')
            api[kind] = {'calls': len(calls), 'request_serialized_bytes': sum(r['request_serialized_bytes'] for r in calls),
                         'reply_serialized_bytes': sum(r['reply_serialized_bytes'] for r in calls)}
        case['runtime_api'] = api
        budget = summary['declared_budget']
        case['measured_cost'] = {k: v for k, v in cost.items() if k not in ('wire', 'emitted')}
        case['measured_cost']['data_total_byte_hops_with_padding'] = sum(cost['inband_per_link_bytes']) + cost['export_padded_bytes']
        case['measured_cost']['scope'] = '全部业务和预热，sink 发出尝试含丢失，最小 Ethernet 填充，无 FCS/前导码/物理间隔'
        case['correctness_data_budget_satisfied'] = all(v <= b for v, b in zip(cost['inband_per_link_bytes'], budget['per_case_inband_per_link_bytes'])) and cost['export_padded_bytes'] <= budget['per_case_export_padded_bytes']
    physical = [0] * 8
    for row in rows(run / 'rpc.jsonl'):
        if row['socket_drop_counter']:
            raise RuntimeError('控制捕获 socket 丢包')
        if row['destination_port'] in range(9471, 9475):
            idx = 2 * (row['destination_port'] - 9471)
        elif row['source_port'] in range(9471, 9475):
            idx = 2 * (row['source_port'] - 9471) + 1
        else:
            raise RuntimeError('控制捕获端口错误')
        physical[idx] += row['frame_bytes']
    groups = {}
    for row in operations:
        kind = row['kind']
        if row['method'] == 'bm_register_reset' and row['arguments'][0].split('.')[-1].startswith('trace_'):
            kind = 'diagnostic_reset'
        group = groups.setdefault(kind, {'calls': 0, 'request_serialized_bytes': 0, 'reply_serialized_bytes': 0})
        group['calls'] += 1
        for key in ('request_serialized_bytes', 'reply_serialized_bytes'):
            group[key] += row[key]
    summary['control_cost'] = {'api_by_kind': groups, 'whole_run_loopback_per_node_direction_bytes': physical,
        'whole_run_loopback_frame_bytes': sum(physical), 'serialization_added_to_physical': False,
        'attribution': '含 CLI/握手/TCP/诊断/GSO，整次运行计费，不按时间归因到单次 RPC',
        'within_correctness_budget': all(v <= summary['declared_budget']['whole_run_control_per_node_direction_bytes'] for v in physical)}
    summary['checks'] = {'output_frames': len(seen), 'business_packets': len(mapping),
                         'online_decisions': sum(r['decision_checks']['checked_decisions'] for r in case_map.values()),
                         'passed': True}
    summary['fair_performance_comparison_completed'] = False
    summary['novelty_established'] = False
    summary['analysis_sha256'] = sha(Path(__file__))
    summary['analysis_dependencies'] = {n: sha(ROOT / n) for n in ('src/collector/strong_codec.py', 'src/collector/codec.py',
                                                                        'src/orchestration/model.py', 'src/orchestration/metrics.py')}
    out.mkdir(parents=True)
    for name in case_map:
        target = out / name
        target.mkdir()
        (target / 'wire-observations.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in costs[name]['wire']))
    archive = out / 'executed-source/experiments'
    archive.mkdir(parents=True)
    shutil.copyfile(__file__, archive / Path(__file__).name)
    for name in summary['analysis_dependencies']:
        target = out / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    summary['raw_artifact_sha256'] = {str(p.relative_to(run)): sha(p) for p in sorted(run.rglob('*')) if p.is_file()}
    summary['artifact_sha256'] = {str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*')) if p.is_file()}
    dump(out / 'summary.json', summary)
    print(json.dumps({'status': summary['status'], 'checks': summary['checks'],
                      'loopback_frame_bytes': sum(physical)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
