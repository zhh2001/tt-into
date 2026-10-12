#!/usr/bin/env python3
"""验证共同状态格式、校准时间区间及 MTU，保存完整原始记录。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from mininet.log import setLogLevel
from build_state import ROOT, build
from topology import create_chain
from run_validation import configure, make_packet, read_registers, load_events, software_version
from collector.codec import checksum, decode_frame as decode_plain
from collector.state_codec import STATE_TYPE, HEADER_BYTES, RECORD_BYTES

SOURCES = ['src/p4/state_int.p4', 'src/collector/codec.py', 'src/collector/state_codec.py',
           'experiments/build_state.py', 'experiments/topology.py', 'experiments/packet_io.py',
           'experiments/state_io.py', 'experiments/run_validation.py', 'experiments/build.py',
           'experiments/run_state_validation.py']


def capture(host, output, name, serve):
    log = open(output / (name + '.log'), 'w')
    ready, stop = output / (name + '.ready'), output / (name + '.stop')
    command = [sys.executable, str(ROOT / 'experiments/state_io.py'), 'capture',
               '--iface', host.defaultIntf().name, '--events', str(output / (name + '.jsonl')),
               '--pcap', str(output / (name + '.pcap')), '--ready', str(ready), '--stop', str(stop)]
    if serve:
        command += ['--serve']
    process = host.popen(command, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 5
    while not ready.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            process.terminate()
            process.wait(timeout=3)
            log.close()
            raise RuntimeError('抓包未启动：' + name)
        time.sleep(0.02)
    return process, stop, log


def send(source, output, packets, phase):
    description = output / ('packets-' + phase + '.json')
    description.write_text(json.dumps(packets, indent=2) + '\n')
    process = source.popen([sys.executable, str(ROOT / 'experiments/state_io.py'), 'send',
                            '--iface', source.defaultIntf().name, '--packets', str(description),
                            '--events', str(output / ('send-' + phase + '.jsonl'))],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _, error = process.communicate(timeout=max(p['offset_ns'] for p in packets) / 1e9 + 10)
    if process.returncode:
        raise RuntimeError(error.decode()[-1000:])


def verify(output, packets, traces, hops):
    dest = load_events(output / 'destination.jsonl')
    received = {row['packet_id']: row for row in dest if row.get('kind') == 'frame' and row.get('src') == '10.0.0.1'}
    reports = {row['packet_id']: row for row in load_events(output / 'collector.jsonl')
               if row.get('ether_type') == STATE_TYPE}
    app = [row['payload_hex'] for row in dest if row.get('kind') == 'udp_application']
    errors, observations = [], []
    for packet in packets:
        pid = packet['packet_id']
        original = decode_plain(bytes.fromhex(packet['frame_hex']))
        got = received.get(pid)
        if not got or got['transport_hex'] != original['transport_hex'] or not got['ip_checksum_ok'] or got['ip_length'] != packet['ip_length'] or got['ttl'] != 64 - hops - 1 or packet['payload_hex'] not in app:
            errors.append(f'业务恢复错误 {pid}')
        expected = min(hops, max(0, (1500 - packet['ip_length'] - HEADER_BYTES) // RECORD_BYTES))
        report = reports.get(pid)
        if expected:
            if not report or report['count'] != expected or report['frame_length'] != 34 + HEADER_BYTES + RECORD_BYTES * expected or report['transport_hex'] or not report['ip_checksum_ok']:
                errors.append(f'导出格式错误 {pid}')
                continue
            if [r['switch_id'] for r in report['records']] != list(range(expected, 0, -1)):
                errors.append(f'逐跳记录错误 {pid}')
        elif report:
            errors.append(f'不应出现记录 {pid}')
        records = {row['switch_id']: row for row in report['records']} if report else {}
        for node, trace in enumerate(traces, 1):
            generation = trace['trace_generated'][pid]
            gate = trace['trace_arrival'][pid]
            value = trace['trace_value'][pid]
            if not gate or generation < gate:
                errors.append(f'门控与生成时间顺序错误 {pid}/{node}')
            if node in records and (records[node]['generated_us'] != generation or records[node]['queue_packets'] != value or records[node]['hop'] != node):
                errors.append(f'状态值或生成时间导出错误 {pid}/{node}')
            observations.append({'packet_id': pid, 'flow': packet['flow'], 'node_id': node,
                                 'gate_local_us': gate, 'generated_local_us': generation,
                                 'queue_packets': value})
    if len(received) != len(packets):
        errors.append('业务报文数量不符')
    (output / 'observations.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in observations))
    positive = [row['queue_packets'] for row in observations if 201 <= row['packet_id'] <= 220 and row['node_id'] == 1]
    if not positive or max(positive) == 0:
        errors.append('排队突发未产生非零队列快照')
    return {'status': 'passed' if not errors else 'failed', 'errors': errors,
            'input_packets': len(packets), 'received_packets': len(received),
            'report_packets': len(reports), 'maximum_queue_packets': max(positive, default=0)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ordinary', action='store_true', help='关闭诊断，仅核对导出及应用')
    args = parser.parse_args()
    if os.geteuid():
        parser.error('需要 sudo 运行')
    setLogLevel('warning')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'run_id': output.name, 'start_utc': datetime.now(timezone.utc).isoformat(),
                'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
                'status': 'running', 'seed': 0, 'hops': 3, 'sink_nodes': 1,
                'diagnostics_enabled': not args.ordinary, 'header_bytes': HEADER_BYTES,
                'record_bytes': RECORD_BYTES, 'threshold_us': 1, 'capacity': 3,
                'warmup': '不排除首包，训练探针不计入回放测量窗口',
                'scope': '共同状态格式与时钟条件检查，非编排性能实验',
                'versions': {'p4c': software_version(['p4c-bm2-ss', '--version']),
                             'bmv2': software_version(['simple_switch', '--version']),
                             'mininet': software_version(['mn', '--version']), 'python': sys.version.split()[0]},
                'start_load_average': list(os.getloadavg())}
    path = output / 'manifest.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    network, handles = None, []
    try:
        network, source, destination, collector, switches = create_chain(
            build(not args.ordinary), output, 3, f'sv{os.getpid() % 65536:04x}',
            22000 + (os.getpid() % 1000) * 10, os.getpid() * 10)
        configure(switches, 3)
        for switch in switches[:-1]:
            switch.cli([command for flow in [1, 2, 3] for command in
                        [f'register_write TTEgress.time_threshold {flow} 1', f'register_write TTEgress.max_count {flow} 3']])
        handles = [capture(destination, output, 'destination', True), capture(collector, output, 'collector', False)]
        phases = [([make_packet(i + 1, flow=i % 2 + 1, offset_ms=i * 20) for i in range(8)], 'calibration-start'),
                  ([make_packet(i + 9, flow=i % 2 + 1, offset_ms=i * 40) for i in range(48)], 'workload'),
                  ([make_packet(i + 57, flow=i % 2 + 1, offset_ms=i * 20) for i in range(8)], 'calibration-end'),
                  ([make_packet(i + 101, ip_length=size, offset_ms=i * 30) for i, size in enumerate([1471, 1472, 1473, 1492, 1493, 1500])], 'mtu')]
        all_packets = []
        for packets, phase in phases:
            send(source, output, packets, phase)
            all_packets += packets
        switches[0].cli(['set_queue_rate 100 2'])
        burst = [make_packet(i + 201, flow=i % 2 + 1) for i in range(20)]
        send(source, output, burst, 'queue-burst')
        all_packets += burst
        time.sleep(0.5)
        from run_validation import finish_capture
        for handle in handles:
            finish_capture(handle)
        handles = []
        if args.ordinary:
            dest = load_events(output / 'destination.jsonl')
            restored = {r['packet_id']: r for r in dest if r.get('kind') == 'frame' and r.get('src') == '10.0.0.1'}
            app = [r['payload_hex'] for r in dest if r.get('kind') == 'udp_application']
            reports = [r for r in load_events(output / 'collector.jsonl') if r.get('ether_type') == STATE_TYPE]
            ok = len(restored) == len(all_packets) and all(p['payload_hex'] in app and restored[p['packet_id']]['transport_hex'] == decode_plain(bytes.fromhex(p['frame_hex']))['transport_hex'] and restored[p['packet_id']]['ip_checksum_ok'] for p in all_packets)
            ok &= len(reports) == 86 and all(r['ip_checksum_ok'] and not r['transport_hex'] and r['frame_length'] == 42 + 20 * r['count'] for r in reports)
            manifest.update(status='passed' if ok else 'failed', input_packets=len(all_packets), received_packets=len(restored), report_packets=len(reports))
        else:
            names = ['trace_arrival', 'trace_generated', 'trace_value']
            traces = [read_registers(switch, names) for switch in switches[:-1]]
            (output / 'registers.json').write_text(json.dumps(traces) + '\n')
            manifest.update(verify(output, all_packets, traces, 3))
    except Exception as exc:
        manifest.update(status='failed', failure=str(exc))
        (output / 'failure.txt').write_text(traceback.format_exc())
    finally:
        from run_validation import finish_capture
        for handle in handles:
            try:
                finish_capture(handle)
            except Exception:
                pass
        if network:
            network.stop()
        manifest['finish_utc'] = datetime.now(timezone.utc).isoformat()
        manifest['finish_load_average'] = list(os.getloadavg())
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in manifest.items() if k in ['status', 'errors', 'failure', 'input_packets', 'report_packets', 'maximum_queue_packets']}, ensure_ascii=False), flush=True)
    return 0 if manifest['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
