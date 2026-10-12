#!/usr/bin/env python3
"""导出共同状态数据，以因果探针计算时钟区间并验证留出报文。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from contextlib import closing
from scapy.utils import RawPcapReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from collector.state_codec import decode_frame, STATE_TYPE
from orchestration.metrics import age_metrics
from orchestration.model import Task


def events(path):
    return [json.loads(row) for row in path.read_text().splitlines() if row]


def calibration(run):
    sends = {row['packet_id']: row for file in run.glob('send-*.jsonl') for row in events(file)}
    reports = {row['packet_id']: row for row in events(run / 'collector.jsonl') if row.get('ether_type') == STATE_TYPE}
    training = list(range(1, 9)) + list(range(57, 65))
    heldout = list(range(9, 57))
    offsets = [row['wall_minus_monotonic_ns'] for row in sends.values()]
    pair_error = max(row['clock_pair_uncertainty_ns'] for row in sends.values())
    drift = max(offsets) - min(offsets)
    if drift + pair_error > 100000:
        raise RuntimeError('宿主时钟相对单调时钟的变化超过预设 100 us 条件')
    slack = 100000 + 1000  # 预设时钟条件与 BMv2 微秒量化，不事后选取。
    origin = sends[9]['send_before_ns']
    nodes, samples = [], []
    for node in range(1, 4):
        def generation(pid):
            return next(r['generated_us'] for r in reports[pid]['records'] if r['switch_id'] == node) * 1000
        lo = max(sends[pid]['send_before_ns'] - generation(pid) - slack for pid in training)
        hi = min(reports[pid]['receive_time_ns'] - generation(pid) + slack for pid in training)
        if lo > hi:
            raise RuntimeError('训练时钟区间无交集，不能给出恒定偏移解释')
        for pid in heldout:
            # 探针留出检验只检验恒偏移条件与因果括区是否相容，不宣称证明时钟精度。
            lower = max(sends[pid]['send_before_ns'], generation(pid) + lo)
            upper = min(reports[pid]['receive_time_ns'], generation(pid) + hi)
            if lower > upper:
                raise RuntimeError(f'留出报文与标定区间不相容 {pid}/{node}')
            samples.append({'packet_id': pid, 'node': node,
                            'generation_lower_us': (lower - origin) / 1000,
                            'generation_upper_us': (upper - origin) / 1000,
                            'arrival_us': (reports[pid]['receive_time_ns'] - origin) / 1000,
                            'queue_packets': next(r['queue_packets'] for r in reports[pid]['records'] if r['switch_id'] == node)})
        nodes.append({'node': node, 'offset_lower_ns': lo, 'offset_upper_ns': hi,
                      'interval_width_us': (hi - lo) / 1000})
    start, end = 0, max(row['arrival_us'] for row in samples) + 40000
    bounds = []
    for node in range(1, 4):
        rows = [r for r in samples if r['node'] == node]
        offset = nodes[node - 1]
        previous = [pid for pid in range(1, 9) if reports[pid]['receive_time_ns'] <= origin]
        initial_pid = max(previous, key=lambda pid: generation_for_node(reports[pid], node)) if previous else None
        initial_lower = initial_upper = None
        if initial_pid is not None:
            local = generation_for_node(reports[initial_pid], node) * 1000
            initial_lower = (max(sends[initial_pid]['send_before_ns'], local + offset['offset_lower_ns']) - origin) / 1000
            initial_upper = (min(reports[initial_pid]['receive_time_ns'], local + offset['offset_upper_ns']) - origin) / 1000
        low_age = age_metrics(Task(node, 100000, initial_generated_us=initial_upper), [(r['generation_upper_us'], r['arrival_us']) for r in rows], start, end)
        high_age = age_metrics(Task(node, 100000, initial_generated_us=initial_lower), [(r['generation_lower_us'], r['arrival_us']) for r in rows], start, end)
        bounds.append({'node': node, 'violation_lower': low_age['violation_fraction'],
                       'violation_upper': high_age['violation_fraction'],
                       'p95_age_lower_us': low_age['p95_age_us'], 'p95_age_upper_us': high_age['p95_age_us'],
                       'unknown_fraction': high_age['unknown_fraction'],
                       'initial_packet_id': initial_pid, 'initial_generation_lower_us': initial_lower,
                       'initial_generation_upper_us': initial_upper})
    return {'training_packet_ids': training, 'heldout_packet_ids': heldout,
            'heldout_checks': len(heldout) * 3, 'host_wall_monotonic_variation_ns': drift,
            'host_clock_pair_error_ns': pair_error, 'predeclared_clock_slack_ns': slack,
            'constant_offset_assumption': True, 'hardware_synchronization_claim': False,
            'origin_ns': origin, 'nodes': nodes, 'samples': samples,
            'age_window_us': [start, end], 'deadline_us': 100000, 'age_bounds': bounds,
            'interpretation': '软件进程恒定时钟偏移与有界宿主时钟变化条件下的区间，探针不属于在线策略的免费观测'}


def generation_for_node(report, node):
    return next(row['generated_us'] for row in report['records'] if row['switch_id'] == node)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--refresh', action='store_true', help='重生成同一原始运行的派生数据')
    args = parser.parse_args()
    run, output = args.run.resolve(), args.output.resolve()
    if run == output or run in output.parents or output in run.parents:
        raise RuntimeError('派生目录必须独立')
    manifest = json.loads((run / 'manifest.json').read_text())
    if output.exists():
        if not args.refresh:
            raise RuntimeError('派生目录已存在')
        prior = json.loads((output / 'summary.json').read_text())
        if prior['run_id'] != manifest['run_id'] or prior['source_sha256'] != manifest['source_sha256']:
            raise RuntimeError('只能重生成同一运行的派生数据')
    if manifest['status'] != 'passed':
        raise RuntimeError('运行未通过，保留失败记录而不导出为通过数据')
    for source, digest in manifest['source_sha256'].items():
        if hashlib.sha256((ROOT / source).read_bytes()).hexdigest() != digest:
            raise RuntimeError('测量源文件已变化：' + source)
    checked, maximum = 0, 0
    for path in run.glob('node-*/*_out.pcap'):
        if path.stat().st_size == 0:
            continue
        with closing(RawPcapReader(str(path))) as reader:
            for frame, _ in reader:
                decoded = decode_frame(frame)
                if decoded:
                    assert decoded['ip_checksum_ok'] and decoded['ip_length'] <= 1500
                    checked += 1
                    maximum = max(maximum, decoded['ip_length'])
    summary = dict(manifest)
    summary['wire_checks'] = {'ipv4_frames': checked, 'maximum_ip_length': maximum, 'passed': True}
    summary['analysis_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    summary['metric_sources_sha256'] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in ['src/orchestration/model.py', 'src/orchestration/metrics.py']}
    if manifest['diagnostics_enabled']:
        summary['calibration'] = calibration(run)
    output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for pattern in ['packets-*.json', 'send-*.jsonl', 'observations.jsonl', 'destination.jsonl', 'collector.jsonl', 'destination.pcap', 'collector.pcap']:
        for source in run.glob(pattern):
            target = output / source.name
            shutil.copyfile(source, target)
            hashes[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
    summary['public_artifact_sha256'] = hashes
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': summary['status'], 'wire_checks': summary['wire_checks'],
                      'calibration_nodes': summary.get('calibration', {}).get('nodes', [])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
