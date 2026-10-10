#!/usr/bin/env python3
"""独立核对固定分支链路、因果年龄区间和完整控制成本，保留实际前史。"""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
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


def analyze_age(directory, case, summary):
    sends = {r['packet_id']: r for r in rows(directory / 'send.jsonl')}
    reports = [r for r in rows(directory / 'collector.jsonl') if r.get('ether_type') == STATE_TYPE]
    samples = {node: {'low': [], 'high': [], 'width': []} for node in (1, 2, 3)}
    offsets = {node: [] for node in (1, 2, 3)}
    origin = case['origin_monotonic_ns']
    for report in reports:
        pid = report['packet_id']
        lower = sends[pid]['send_before_monotonic_ns']
        upper = report['receive_monotonic_ns']
        if lower > upper or report['socket_drop_counter']:
            raise RuntimeError('单调因果关系或采集丢包计数异常')
        for item in report['records']:
            node = item['switch_id']
            samples[node]['low'].append(((lower - origin) / 1000, (upper - origin) / 1000))
            samples[node]['high'].append(((upper - origin) / 1000, (upper - origin) / 1000))
            samples[node]['width'].append((upper - lower) / 1000)
            offsets[node].append((lower - item['generated_us'] * 1000, upper - item['generated_us'] * 1000))
    values = []
    for (start, end), deadlines in zip(summary['requirements_window_offset_us'], summary['deadlines_us']):
        tasks = []
        for node, deadline in enumerate(deadlines, 1):
            low = age_metrics(Task(node, deadline), samples[node]['low'], start, end)
            high = age_metrics(Task(node, deadline), samples[node]['high'], start, end)
            tasks.append({'node': node, 'deadline_us': deadline, 'violation_lower': high['violation_fraction'],
                          'violation_upper': low['violation_fraction'], 'p95_age_lower_us': high['p95_age_us'],
                          'p95_age_upper_us': low['p95_age_us'], 'unknown_fraction': low['unknown_fraction'],
                          'fulfilled_by_upper_age': low['fulfilled'],
                          'maximum_generation_interval_width_us': max(samples[node]['width'], default=None)})
        values.append({'window_us': [start, end], 'tasks': tasks,
                       'fulfilled_by_upper_age': sum(r['fulfilled_by_upper_age'] for r in tasks)})
    compatible = {}
    for node, bounds in offsets.items():
        lo, hi = max(v[0] for v in bounds), min(v[1] for v in bounds)
        compatible[str(node)] = {'compatible': lo <= hi, 'intersection_width_us': max(0, hi - lo) / 1000,
                                 'incompatible_gap_us': max(0, lo - hi) / 1000}
    return {'phases': values, 'clock_domain': '同一宿主 CLOCK_MONOTONIC，发送前 <= 生成 <= recvmsg 完成',
            'switch_timestamps_used_for_age': False, 'availability': '采集进程 recvmsg 完成',
            'initial_sample': '使用每项实际报告前史，未人工置零年龄', 'local_offset_compatibility': compatible,
            'maximum_send_lateness_us': max(r['schedule_lateness_ns'] for r in sends.values()) / 1000}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    run, out = a.run.resolve(), a.output.resolve()
    if out.exists() or run in out.parents or out in run.parents:
        raise RuntimeError('派生目录须独立且不存在')
    summary = json.loads((run / 'manifest.json').read_text())
    if summary['status'] != 'passed':
        raise RuntimeError('原始运行未通过')
    for name, digest in summary['source_sha256'].items():
        if sha(run / 'executed-source' / name) != digest:
            raise RuntimeError('归档测量源码损坏 ' + name)
    if sha(run / 'declared-budget.json') != summary['budget_sha256']:
        raise RuntimeError('预声明预算被修改')
    packets, cases = {}, {}
    costs = {}
    for case in summary['cases']:
        name = case['name']
        cases[name] = case
        inputs = json.loads((run / name / 'packets.json').read_text())
        for row in inputs:
            if row['packet_id'] in packets:
                raise RuntimeError('全程包标识重复')
            packets[row['packet_id']] = (name, row)
        case['age'] = analyze_age(run / name, case, summary)
        costs[name] = {'inband_per_link_bytes': [0, 0, 0], 'export_bytes': 0,
                       'export_bytes_with_minimum_ethernet_padding': 0, 'frames_by_node_port': Counter(), 'wire': []}
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
                if pid not in packets or (pid, node, port) in seen:
                    raise RuntimeError('链路未知包或重复包')
                seen.add((pid, node, port))
                name, original = packets[pid]
                cost = costs[name]
                if not row['ip_checksum_ok'] or row['ip_length'] > 1500:
                    raise RuntimeError('IPv4 校验和或 MTU 错误')
                if node < 4:
                    if node not in original['path']:
                        raise RuntimeError('真实链路超出包物理路径')
                    extra = row['ip_length'] - original['ip_length']
                    # 独立按线上的字段计费，前缀 K=1 只允许 header 或 header+record。
                    if row['ether_type'] != STATE_TYPE or extra != 4 + 12 * row['count'] or row['count'] > 1:
                        raise RuntimeError('真实前缀格式或附加字节不符')
                    cost['inband_per_link_bytes'][node - 1] += extra
                    selected = [r['switch_id'] for r in row['records']]
                    if not set(selected) <= set(original['path'][:original['path'].index(node) + 1]):
                        raise RuntimeError('链路记录尚未经过采样对象')
                elif port == 2:
                    extra = row['ip_length'] - original['ip_length']
                    if extra != 0 or row['ether_type'] != 0x0800:
                        raise RuntimeError('业务包未剥离元数据')
                else:
                    extra = len(frame)
                    if row['ether_type'] != STATE_TYPE or row['count'] != 1 or row['ip_length'] != 36:
                        raise RuntimeError('导出帧格式不符')
                    cost['export_bytes'] += len(frame)
                    cost['export_bytes_with_minimum_ethernet_padding'] += max(60, len(frame))
                cost['frames_by_node_port'][f'{node}:{port}'] += 1
                cost['wire'].append({'packet_id': pid, 'node': node, 'port': port,
                                     'frame_bytes': len(frame), 'ip_bytes': row['ip_length'],
                                     'extra_bytes': extra, 'count': row.get('count', 0),
                                     'records': row.get('records', [])})
    budget = summary['declared_budget']
    data_ok = True
    for name, case in cases.items():
        cost = costs[name]
        originals = [row for c, row in packets.values() if c == name]
        expected = {f'{n}:2': sum(n in row['path'] for row in originals) for n in (1, 2, 3)}
        expected.update({'4:2': len(originals), '4:3': case['report_count']})
        if dict(cost['frames_by_node_port']) != expected:
            raise RuntimeError('真实路径或导出数量不符 ' + name)
        # 从最终收报恢复前缀选择后，独立重算每条路径的累计记录宽度。
        reports = {r['packet_id']: r for r in rows(run / name / 'collector.jsonl') if r.get('ether_type') == STATE_TYPE}
        predicted = [0, 0, 0]
        for original in originals:
            selected = {r['switch_id'] for r in reports.get(original['packet_id'], {}).get('records', [])}
            for index, node in enumerate(original['path']):
                predicted[node - 1] += 4 + 12 * len(selected.intersection(original['path'][:index + 1]))
        if predicted != cost['inband_per_link_bytes']:
            raise RuntimeError('独立的前缀传播费用与链路不一致')
        cost['data_total_byte_hops_with_padding'] = sum(predicted) + cost['export_bytes_with_minimum_ethernet_padding']
        cost['scope'] = '全部业务包及预热，附加带内字节和导出帧，不含 FCS/前导码/物理间隔'
        cost['within_declared_budget'] = all(v <= limit for v, limit in zip(predicted, budget['per_case_inband_per_link_bytes'])) and cost['export_bytes_with_minimum_ethernet_padding'] <= budget['per_case_export_bytes_with_minimum_ethernet_padding']
        data_ok &= cost['within_declared_budget']
        case['measured_cost'] = {k: v for k, v in cost.items() if k != 'wire'}
    physical = [0] * 8
    control_rows = rows(run / 'rpc.jsonl')
    for row in control_rows:
        if row['socket_drop_counter']:
            raise RuntimeError('控制捕获发生 socket 丢包')
        if row['destination_port'] in range(9471, 9475):
            index = 2 * (row['destination_port'] - 9471)
        elif row['source_port'] in range(9471, 9475):
            index = 2 * (row['source_port'] - 9471) + 1
        else:
            raise RuntimeError('未知控制端口')
        physical[index] += row['frame_bytes']
    api_groups = {}
    for row in rows(run / 'rpc-operations.jsonl'):
        kind = {'cold_start_reset': 'installation', 'diagnostic_read': 'validation', 'online_update': 'update'}.get(row['kind'], row['kind'])
        group = api_groups.setdefault(kind, {'calls': 0, 'request_serialized_bytes': 0, 'reply_serialized_bytes': 0, 'rpc_elapsed_us': []})
        group['calls'] += 1
        for key in ('request_serialized_bytes', 'reply_serialized_bytes'):
            group[key] += row[key]
        group['rpc_elapsed_us'].append((row['after_monotonic_ns'] - row['before_monotonic_ns']) / 1000)
    control_ok = all(v <= budget['whole_run_control_per_node_direction_bytes'] for v in physical)
    summary['control_cost'] = {'metered_rpc_by_kind': api_groups, 'captured_control_per_node_direction_bytes': physical,
                               'captured_control_total_bytes': sum(physical), 'within_declared_budget': control_ok,
                               'physical_attribution': 'unattributed，含 CLI/握手/TCP/诊断/GSO，不按收包时间归因 RPC',
                               'serialization_added_to_physical': False}
    summary['wire_checks'] = {'passed': True, 'checked_ipv4_output_frames': len(seen)}
    summary['budget_check'] = {'data': data_ok, 'whole_run_control': control_ok,
                               'scope': '预声明宽松正确性预算，不是等预算性能结论'}
    summary['scheduled_tt_causal_age_feasible'] = all(p['fulfilled_by_upper_age'] == 3 for case in cases.values()
                                                    if case['dynamic'] for p in case['age']['phases'])
    summary['scheduled_tt_correctness_budget_feasible'] = summary['scheduled_tt_causal_age_feasible'] and data_ok and control_ok
    summary['fair_performance_comparison_completed'] = False
    summary['novelty_established'] = False
    summary['analysis_sha256'] = sha(Path(__file__))
    summary['analysis_dependencies'] = {name: sha(ROOT / name) for name in ['src/collector/strong_codec.py', 'src/collector/codec.py', 'src/orchestration/metrics.py', 'src/orchestration/model.py']}
    out.mkdir(parents=True)
    for name in cases:
        target = out / name
        target.mkdir()
        for filename in ['packets.json', 'send.jsonl', 'destination.jsonl', 'collector.jsonl', 'destination.pcap', 'collector.pcap', 'observations.jsonl', 'result.json']:
            if (run / name / filename).exists():
                shutil.copyfile(run / name / filename, target / filename)
        (target / 'wire-observations.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in costs[name]['wire']))
    shutil.copyfile(run / 'declared-budget.json', out / 'declared-budget.json')
    shutil.copyfile(run / 'rpc.jsonl', out / 'rpc-frame-counts.jsonl')
    # 公开操作保留必要字段，不复制原始 RPC 载荷或进程日志。
    shutil.copyfile(run / 'rpc-operations.jsonl', out / 'rpc-operations.jsonl')
    for name in summary['source_sha256']:
        target = out / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(run / 'executed-source' / name, target)
    target = out / 'executed-source/experiments/analyze_fork_validation.py'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__), target)
    summary['artifact_sha256'] = {str(path.relative_to(out)): sha(path) for path in out.rglob('*') if path.is_file()}
    dump(out / 'summary.json', summary)
    print(json.dumps({'run': summary['run_id'], 'frames': len(seen), 'phase_task_fulfilled':
                      {name: [p['fulfilled_by_upper_age'] for p in case['age']['phases']] for name, case in cases.items()},
                      'scheduled_tt_correctness_budget_feasible': summary['scheduled_tt_correctness_budget_feasible']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
