#!/usr/bin/env python3
"""核对真实链路字节，输出带时钟条件的年龄区间与停流参照。"""
import argparse
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import sys
from scapy.utils import RawPcapReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from collector.state_codec import decode_frame, STATE_TYPE
from orchestration.metrics import age_metrics
from orchestration.model import Task, Packet
from orchestration.oracle import solve


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def load_case(directory):
    packets = {p['packet_id']: p for file in directory.glob('packets-*.json') for p in json.loads(file.read_text())}
    sends = {p['packet_id']: p for file in directory.glob('send-*.jsonl') for p in rows(file)}
    received = {p['packet_id']: p for p in rows(directory / 'destination.jsonl') if p.get('kind') == 'frame' and p.get('src') == '10.0.0.1'}
    reports = {p['packet_id']: p for p in rows(directory / 'collector.jsonl') if p.get('ether_type') == STATE_TYPE}
    return packets, sends, received, reports


def wire_measurements(run, cases):
    records = {name: [] for name in cases}
    costs = {name: {'inband_per_link_bytes': [0, 0, 0], 'export_bytes': 0, 'destination_metadata_bytes': 0} for name in cases}
    frame_counts = {name: [0, 0, 0, 0, 0] for name in cases}
    total, maximum = 0, 0
    for path in sorted(run.glob('node-*/*_out.pcap')):
        if not path.stat().st_size:
            continue
        node = int(path.parent.name.split('-')[1])
        if path.name.endswith('-eth2_out.pcap'):
            edge = node - 1
        elif node == 4 and path.name.endswith('-eth3_out.pcap'):
            edge = 4
        else:
            continue
        with closing(RawPcapReader(str(path))) as reader:
            for frame, metadata in reader:
                decoded = decode_frame(frame)
                if not decoded or decoded['src'] != '10.0.0.1':
                    continue
                assert decoded['ip_checksum_ok'] and decoded['ip_length'] <= 1500
                stamp = metadata.sec * 1000000000 + metadata.usec * 1000
                matches = []
                for name, (packets, sends, received, _) in cases.items():
                    left = min(p['send_before_ns'] for p in sends.values()) - 10000000
                    right = max(p['receive_time_ns'] for p in received.values()) + 10000000
                    if left <= stamp <= right:
                        matches.append(name)
                if len(matches) != 1:
                    raise RuntimeError('链路帧不能唯一关联运行窗口')
                name = matches[0]
                pid = decoded['packet_id']
                if pid not in cases[name][0]:
                    raise RuntimeError('链路存在未登记 IPv4 包')
                original_length = cases[name][0][pid]['ip_length']
                if edge < 3:
                    overhead = decoded['ip_length'] - original_length
                    assert overhead >= 0
                    costs[name]['inband_per_link_bytes'][edge] += overhead
                elif edge == 3:
                    overhead = decoded['ip_length'] - original_length
                    costs[name]['destination_metadata_bytes'] += overhead
                    assert overhead == 0
                else:
                    assert decoded['ether_type'] == STATE_TYPE
                    overhead = len(frame)
                    costs[name]['export_bytes'] += overhead
                frame_counts[name][edge] += 1
                records[name].append({'packet_id': pid, 'node': node, 'output_port': 3 if edge == 4 else 2,
                                      'time_ns': stamp, 'ip_bytes': decoded['ip_length'],
                                      'frame_bytes': len(frame), 'count': decoded.get('count', 0),
                                      'added_bytes': overhead, 'checksum_ok': True})
                total += 1
                maximum = max(maximum, decoded['ip_length'])
    for name, (packets, _, _, reports) in cases.items():
        assert frame_counts[name][:4] == [len(packets)] * 4
        assert frame_counts[name][4] == len(reports)
        costs[name]['total_byte_hops'] = sum(costs[name]['inband_per_link_bytes']) + costs[name]['export_bytes']
        costs[name]['byte_convention'] = '带内附加字节和 Ethernet 导出帧字节，不含 FCS、前导码及物理间隔'
        costs[name]['budget_scope'] = '当前抓包全过程，包括校准与预热；年龄窗口单独给出'
    return records, costs, {'checked_frames': total, 'maximum_ip_length': maximum, 'passed': True}


def age_bounds(name, data):
    packets, sends, _, reports = data
    training = list(range(3000, 3016))
    clock_offsets = [p['wall_minus_monotonic_ns'] for p in sends.values()]
    drift = max(clock_offsets) - min(clock_offsets)
    pair_error = max(p['clock_pair_uncertainty_ns'] for p in sends.values())
    if drift + pair_error > 100000:
        return {'age_evaluated': False, 'reason': '宿主 wall/monotonic 变化超出预设 100 us 条件',
                'host_wall_monotonic_variation_ns': drift, 'host_clock_pair_error_ns': pair_error,
                'age_excluded_due_to_clock_conditions': True,
                'interpretation': '保留运行和成本，不放宽条件，不输出不可靠年龄指标'}
    offsets = []
    for node in range(1, 4):
        def generation(pid):
            return next(r['generated_us'] for r in reports[pid]['records'] if r['switch_id'] == node) * 1000
        lower = max(sends[pid]['send_before_ns'] - generation(pid) - 101000 for pid in training)
        upper = min(reports[pid]['receive_time_ns'] - generation(pid) + 101000 for pid in training)
        if lower > upper:
            raise RuntimeError('恒定偏移区间不相容')
        offsets.append({'node': node, 'lower_ns': lower, 'upper_ns': upper, 'width_us': (upper - lower) / 1000})
    first = 25 if name == 'flow_stop_shared' else 9
    if first not in sends or name in ['online_authorization', 'permission_mtu']:
        return {'training_packet_ids': training, 'offset_intervals': offsets, 'age_evaluated': False}
    workload = sorted(pid for pid in packets if pid < 3000)
    last_first_flow = max(pid for pid in workload if packets[pid]['flow'] == 1)
    origin = sends[first]['send_before_ns']
    end = (sends[last_first_flow]['send_before_ns'] + 200000000 - origin) / 1000
    values, sample_bounds = [], []
    for node, deadline in enumerate([250000, 450000, 650000], 1):
        offset = offsets[node - 1]
        samples = []
        initial_lower = initial_upper = None
        initial_local = None
        initial_pid = None
        for pid, report in reports.items():
            # 末端训练包只标定时钟，不向年龄窗口提供更新。
            if pid >= 3008:
                continue
            for record in report['records']:
                if record['switch_id'] != node:
                    continue
                lower = max(sends[pid]['send_before_ns'], record['generated_us'] * 1000 + offset['lower_ns'])
                upper = min(report['receive_time_ns'], record['generated_us'] * 1000 + offset['upper_ns'])
                if lower > upper:
                    raise RuntimeError('工作包与训练区间不相容')
                arrival = (report['receive_time_ns'] - origin) / 1000
                g_low, g_high = (lower - origin) / 1000, (upper - origin) / 1000
                sample_bounds.append({'packet_id': pid, 'node': node, 'generation_lower_us': g_low,
                                      'generation_upper_us': g_high, 'arrival_us': arrival})
                if arrival <= 0:
                    if initial_local is None or record['generated_us'] > initial_local:
                        initial_lower, initial_upper = g_low, g_high
                        initial_local, initial_pid = record['generated_us'], pid
                elif arrival <= end:
                    samples.append((g_low, g_high, arrival))
        low = age_metrics(Task(node, deadline, initial_generated_us=initial_upper), [(g_high, a) for _, g_high, a in samples], 0, end)
        high = age_metrics(Task(node, deadline, initial_generated_us=initial_lower), [(g_low, a) for g_low, _, a in samples], 0, end)
        values.append({'node': node, 'deadline_us': deadline, 'violation_lower': low['violation_fraction'],
                       'violation_upper': high['violation_fraction'], 'p95_age_lower_us': low['p95_age_us'],
                       'p95_age_upper_us': high['p95_age_us'], 'unknown_fraction': high['unknown_fraction'],
                       'initial_packet_id': initial_pid, 'initial_generation_lower_us': initial_lower,
                       'initial_generation_upper_us': initial_upper,
                       'guaranteed_fulfilled_under_clock_conditions': high['violation_fraction'] <= 1e-10})
    return {'age_evaluated': True, 'training_packet_ids': training, 'age_origin_packet_id': first,
            'age_window_us': [0, end], 'origin_ns': origin, 'offset_intervals': offsets,
            'host_wall_monotonic_variation_ns': drift, 'host_clock_pair_error_ns': pair_error,
            'predeclared_slack_ns': 101000, 'constant_offset_assumption': True,
            'samples': sample_bounds, 'tasks': values,
            'fulfilled_tasks_upper_age_bound': sum(v['guaranteed_fulfilled_under_clock_conditions'] for v in values),
            'interpretation': '保留实际已收前史，不用校准区间中点冒充真值；末端探针不参与年龄更新'}


def stop_reference(data):
    packets, sends, received, _ = data
    pids = sorted(pid for pid in packets if 25 <= pid < 3000)
    origin = sends[pids[0]]['send_before_ns']
    delay_bound = max((received[pid]['receive_time_ns'] - sends[pid]['send_before_ns']) / 1000 for pid in pids)
    end = (sends[pids[-1]]['send_before_ns'] - origin) / 1000 + 200000
    tasks = [Task(i + 1, int(d + delay_bound + 1), w, initial_generated_us=0) for i, (d, w) in enumerate(zip([250000, 450000, 650000], [3, 2, 1]))]
    trajectory = []
    for pid in pids:
        t = (sends[pid]['send_before_ns'] - origin) / 1000
        trajectory.append(Packet(pid, 1, (1, 2, 3), (t,) * 3, (t,) * 3, t, capacity=1))
    all_tasks = solve(tasks, trajectory, 0, 0, end, force_all=True)
    admission = solve(tasks, trajectory, 0, 0, end)
    return {'kind': 'favorable_fixed_trace_reference', 'threshold_us': 0,
            'initial_state': '在参考中放宽为窗口起点全部刚更新，不作为真实年龄评价',
            'delay_relaxation': '报告零延迟，并将期限增加实测发送至业务接收上界，消除生成时刻提前的劣势',
            'observed_delay_bound_us': delay_bound,
            'conditional_bound': '只针对给定包轨迹及生成滞后不超过该实测上界的执行，不保证其他策略的排队变化仍在此界内',
            'tasks': [asdict(task) for task in tasks], 'packets': [asdict(p) for p in trajectory],
            'age_window_us': [0, end], 'all_tasks_result': all_tasks, 'admission_result': admission}


def plot(summary, directory):
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams.update({'pdf.fonttype': 42, 'ps.fonttype': 42})
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties, findfont
    font_path = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    if not font_path.exists() or Path(findfont(FontProperties(fname=str(font_path)))).resolve() != font_path:
        raise RuntimeError('Times New Roman 字体未解析')
    font = FontProperties(fname=str(font_path), size=10)
    names = ['tt_flow_prefix', 'shared_aligned', 'static_aligned', 'tt_flow_tuned', 'flow_stop_shared']
    cases = {c['name']: c for c in summary['cases']}
    if not all(name in cases for name in names):
        return
    fig, axis = plt.subplots(figsize=(7.2, 3.0))
    for node, color in enumerate(['#446b8b', '#9a8053', '#749276']):
        lows = [cases[name]['age']['tasks'][node]['violation_lower'] for name in names]
        highs = [cases[name]['age']['tasks'][node]['violation_upper'] for name in names]
        x = [i + (node - 1) * 0.23 for i in range(len(names))]
        axis.bar(x, highs, width=0.22, color=color, hatch=['', '//', '..'][node],
                 edgecolor='black', linewidth=0.3, label=f'Task {node + 1}')
        axis.errorbar(x, highs, yerr=[[b - a for a, b in zip(lows, highs)], [0] * len(highs)], fmt='none', ecolor='black', capsize=2)
    axis.set_xticks(range(5), ['TT prefix', 'Shared gate', 'Static mask', 'TT tuned', 'Flow stop (shared)'])
    axis.set_ylim(0, 1.05)
    axis.set_ylabel('Age violation fraction', fontproperties=font)
    axis.set_title('Single-run BMv2 validation with clock intervals', fontproperties=font)
    for label in axis.get_xticklabels() + axis.get_yticklabels():
        label.set_fontproperties(font)
    axis.legend(prop=font, loc='upper center', ncol=3)
    axis.spines[['top', 'right']].set_visible(False)
    fig.tight_layout()
    directory.mkdir(parents=True, exist_ok=True)
    fig.savefig(directory / 'baseline-execution.png', dpi=300, metadata={'Software': 'Matplotlib'})
    fig.savefig(directory / 'baseline-execution.pdf', metadata={'Title': 'BMv2 baseline execution validation', 'Author': '', 'Creator': ''})
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--figures', type=Path)
    args = parser.parse_args()
    run, output = args.run.resolve(), args.output.resolve()
    if output.exists() or run == output or run in output.parents or output in run.parents:
        raise RuntimeError('派生目录须独立且不存在')
    summary = json.loads((run / 'manifest.json').read_text())
    if summary['status'] != 'passed':
        raise RuntimeError('原始运行未通过')
    for name, digest in summary['source_sha256'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('测量源文件变化：' + name)
    cases = {case['name']: load_case(run / case['name']) for case in summary['cases']}
    wire, costs, check = wire_measurements(run, cases)
    summary['wire_checks'] = check
    summary['analysis_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    metric_sources = ['src/orchestration/model.py', 'src/orchestration/metrics.py', 'src/orchestration/oracle.py']
    summary['analysis_dependency_sha256'] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in metric_sources}
    controls = rows(run / 'control-events.jsonl')
    rpc = rows(run / 'rpc-events.jsonl')
    maximum_drops = max((row['socket_drop_counter'] for row in rpc), default=0)
    kinds = {}
    for record in rpc:
        matching = [event for event in controls if event['before_ns'] <= record['time_ns'] <= event['after_ns']]
        kind = matching[-1]['kind'] if matching else 'unclassified_startup_or_connection_tail'
        kind_row = kinds.setdefault(kind, {'frame_count': 0, 'loopback_captured_frame_bytes': 0})
        kind_row['frame_count'] += 1
        kind_row['loopback_captured_frame_bytes'] += record['frame_bytes']
    summary['control_capture'] = {'direction': 'Linux PACKET_OUTGOING，仅计每帧一次',
                                  'socket_drop_counter': maximum_drops, 'capture_complete_under_socket_counter': maximum_drops == 0,
                                  'total_captured_frame_bytes': sum(row['frame_bytes'] for row in rpc), 'by_kind': kinds,
                                  'interpretation': '本机 Thrift、TCP、CLI 初始化及读取流量，含 loopback/GSO 语义，不能当作部署链路控制成本'}
    output.mkdir(parents=True)
    hashes = {}

    def copy(source, target):
        shutil.copyfile(source, target)
        hashes[str(target.relative_to(output))] = hashlib.sha256(target.read_bytes()).hexdigest()

    for case in summary['cases']:
        name = case['name']
        directory = output / name
        directory.mkdir()
        measured = costs[name]
        if case['predicted_cost']:
            for key in ['inband_per_link_bytes', 'export_bytes', 'total_byte_hops']:
                if measured[key] != case['predicted_cost'][key]:
                    raise RuntimeError('链路实测字节与推导不符：' + name + '/' + key)
        case['measured_cost'] = measured
        case['age'] = age_bounds(name, cases[name])
        if name == 'flow_stop_shared':
            case['favorable_reference'] = stop_reference(cases[name])
        for pattern in ['packets-*.json', 'send-*.jsonl', 'observations.jsonl', 'destination.jsonl', 'collector.jsonl', 'destination.pcap', 'collector.pcap']:
            for source in (run / name).glob(pattern):
                copy(source, directory / source.name)
        target = directory / 'wire-observations.jsonl'
        target.write_text(''.join(json.dumps(row) + '\n' for row in wire[name]))
        hashes[str(target.relative_to(output))] = hashlib.sha256(target.read_bytes()).hexdigest()
    copy(run / 'control-events.jsonl', output / 'control-events.jsonl')
    copy(run / 'rpc-events.jsonl', output / 'rpc-frame-counts.jsonl')
    summary['public_artifact_sha256'] = hashes
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    if args.figures:
        plot(summary, args.figures)
    print(json.dumps({'cases': len(summary['cases']), 'wire_checks': check, 'rpc_socket_drops': maximum_drops,
                      'age_fulfilled_tasks': {case['name']: case['age'].get('fulfilled_tasks_upper_age_bound') for case in summary['cases']}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
