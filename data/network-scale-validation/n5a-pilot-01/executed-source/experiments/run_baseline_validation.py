#!/usr/bin/env python3
"""实测共享门控、静态许可和逐流容量，独立核对诊断及链路字节。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
from mininet.log import setLogLevel
from scapy.all import Ether, IP, UDP, Raw
from build_baseline import ROOT, build
from topology import create_chain
from run_validation import configure, read_registers, load_events, finish_capture, software_version
from run_state_validation import capture, send
from collector.codec import decode_frame as decode_plain, checksum
from collector.state_codec import STATE_TYPE
from orchestration.baseline_check import predict

TRACE_NAMES = ['trace_arrival', 'trace_generated', 'trace_value', 'trace_decision',
               'trace_count_before', 'trace_gate_index', 'trace_mask', 'trace_epoch']
SOURCES = ['src/p4/baseline_int.p4', 'src/collector/codec.py', 'src/collector/state_codec.py',
           'src/orchestration/baseline_check.py', 'experiments/build_baseline.py',
           'experiments/topology.py', 'experiments/packet_io.py', 'experiments/state_io.py',
           'experiments/run_validation.py', 'experiments/build.py', 'experiments/run_state_validation.py',
           'experiments/build_state.py', 'experiments/run_baseline_validation.py', 'experiments/capture_rpc.py']


def configuration(scope='flow', thresholds=None, capacities=None, masks=None, epoch=1):
    thresholds = thresholds or [100000] * 3
    capacities = capacities or [1] * 3
    return {'scope': scope,
            'thresholds': [value if isinstance(value, dict) else {str(f): value for f in [1, 2, 3]} for value in thresholds],
            'capacities': [value if isinstance(value, dict) else {str(f): value for f in [1, 2, 3]} for value in capacities],
            'masks': {str(f): (masks or {}).get(f, 255) for f in [1, 2, 3]},
            'epochs': {str(f): epoch for f in [1, 2, 3]}}


def packet(pid, flow=1, offset_ms=0, ip_length=120):
    sport = {1: 10101, 2: 10102, 3: 10303}[flow]
    payload = pid.to_bytes(4, 'big') + bytes([flow]) * (ip_length - 32)
    frame = bytes(Ether(src='02:00:00:00:00:01', dst='02:00:00:00:00:02') /
                  IP(src='10.0.0.1', dst='10.0.0.2', ttl=64, id=pid) /
                  UDP(sport=sport, dport=9000) / Raw(payload))
    return {'packet_id': pid, 'flow': flow, 'offset_ns': offset_ms * 1000000,
            'ip_length': ip_length, 'frame_hex': frame.hex(), 'payload_hex': payload.hex()}


def paired(phase_ms=5, pairs=12):
    return [packet(2 * group + flow, flow, group * 200 + (phase_ms if flow == 2 else 0))
            for group in range(pairs) for flow in [1, 2]]


def cases():
    tuned = configuration(thresholds=[{'1': 1, '2': 1, '3': 1}, {'1': 1, '2': 300000, '3': 1}, {'1': 1, '2': 1, '3': 1}],
                          capacities=[{'1': 1, '2': 0, '3': 3}, {'1': 0, '2': 1, '3': 3}, {'1': 0, '2': 1, '3': 3}])
    return [
        ('tt_flow_prefix', configuration(), paired(), [24, 0, 0], None),
        ('shared_aligned', configuration('port', [100000, 300000, 1]), paired(), [12, 6, 6], None),
        ('shared_staggered', configuration('port', [180000, 300000, 1]), paired(100), [12, 6, 6], None),
        ('static_aligned', configuration(thresholds=[1, 300000, 1], masks={1: 1, 2: 6}), paired(), [12, 6, 6], None),
        ('static_staggered', configuration(thresholds=[1, 300000, 1], masks={1: 1, 2: 6}), paired(100), [12, 6, 6], None),
        ('tt_flow_tuned', tuned, paired(), [12, 6, 6], None),
        ('flow_stop_shared', configuration('port', [100000, 300000, 1]), paired(), None, 'stop'),
        ('online_authorization', configuration(thresholds=[1, 1, 1], masks={1: 1}, epoch=10),
         [packet(i + 1, offset_ms=i * 200) for i in range(4)], [5, 4, 0], 'update'),
        ('permission_mtu', configuration(thresholds=[1, 1, 1], masks={1: 4}),
         [packet(i + 1, offset_ms=i * 30, ip_length=size) for i, size in enumerate([1471, 1472, 1473, 1492, 1493, 1500])], [0, 0, 2], None),
    ]


class Control:
    def __init__(self, switches, output):
        self.switches, self.output = switches, output
        self.handles = {}
        self.events = []

    def command(self, switch, commands, kind='installation'):
        before = time.time_ns()
        result = switch.cli(commands)
        self.events.append({'node': self.switches.index(switch) + 1, 'kind': kind,
                            'before_ns': before, 'after_ns': time.time_ns(), 'commands': commands})
        (self.output / 'control-events.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in self.events))
        return result

    def authorize(self, flow, mask, epoch, kind='installation'):
        switch = self.switches[0]
        if flow not in self.handles:
            result = self.command(switch, [f'table_add TTIngress.source_policy authorize {flow} => {mask} {epoch}'], kind)
            match = re.search(r'with handle\s+(\d+)', result)
            if not match:
                raise RuntimeError('未取得源端授权表项句柄')
            self.handles[flow] = int(match.group(1))
        else:
            self.command(switch, [f'table_modify TTIngress.source_policy authorize {self.handles[flow]} {mask} {epoch}'], kind)

    def apply(self, config, kind='installation'):
        for index, switch in enumerate(self.switches[:-1]):
            commands = [f'table_set_default TTEgress.gate_policy choose_scope {int(config["scope"] == "port")}']
            for flow in [1, 2, 3]:
                gate = 258 if config['scope'] == 'port' else flow
                commands += [f'register_write TTEgress.time_threshold {gate} {config["thresholds"][index][str(flow)]}',
                             f'register_write TTEgress.max_count {flow} {config["capacities"][index][str(flow)]}']
            self.command(switch, commands, kind)
        for flow in [1, 2, 3]:
            self.authorize(flow, config['masks'][str(flow)], config['epochs'][str(flow)], kind)

    def state(self):
        before = time.time_ns()
        values = [read_registers(switch, ['last_timestamp', 'has_sample']) for switch in self.switches[:-1]]
        self.events.append({'node': 0, 'kind': 'diagnostic_read', 'before_ns': before,
                            'after_ns': time.time_ns(), 'commands': ['read last_timestamp and has_sample on all sampling nodes']})
        return values


def verify(output, packets, traces, states, counts_expected):
    dest = load_events(output / 'destination.jsonl')
    frames = [row for row in dest if row.get('kind') == 'frame' and row.get('src') == '10.0.0.1']
    received = {row['packet_id']: row for row in frames}
    report_events = [row for row in load_events(output / 'collector.jsonl') if row.get('ether_type') == STATE_TYPE]
    reports = {row['packet_id']: row for row in report_events}
    app = [row['payload_hex'] for row in dest if row.get('kind') == 'udp_application']
    errors = []
    if len(frames) != len(packets) or len(received) != len(packets) or len(reports) != len(report_events):
        errors.append('报文或报告重复、缺失')
    for p in packets:
        got, original = received.get(p['packet_id']), decode_plain(bytes.fromhex(p['frame_hex']))
        if not got or got['transport_hex'] != original['transport_hex'] or got['ip_length'] != p['ip_length'] or got['ttl'] != 60 or not got['ip_checksum_ok'] or p['payload_hex'] not in app:
            errors.append(f'业务恢复或应用错误 {p["packet_id"]}')
        if p['packet_id'] in reports:
            report = reports[p['packet_id']]
            if report['transport_hex'] or not report['ip_checksum_ok'] or report['frame_length'] != 42 + 20 * report['count']:
                errors.append(f'报告截断错误 {p["packet_id"]}')
            config = p['configuration']
            if report['allow_mask'] != config['masks'][str(p['flow'])] or report['epoch'] != config['epochs'][str(p['flow'])]:
                errors.append(f'权限或版本错误 {p["packet_id"]}')
    counts = [0, 0, 0]
    for pid, report in reports.items():
        if pid < 3000:
            for record in report['records']:
                counts[record['switch_id'] - 1] += 1
    if counts_expected is not None and counts != counts_expected:
        errors.append(f'构造场景计数不符 {counts} != {counts_expected}')
    predicted = None
    if traces is not None:
        predicted = predict(packets, traces, 3)
        for row in predicted['observations']:
            pid, index = row['packet_id'], row['node'] - 1
            trace = traces[index]
            for actual, expected in [('trace_decision', 'decision'), ('trace_count_before', 'count_before'),
                                     ('trace_gate_index', 'gate_index'), ('trace_mask', 'mask'), ('trace_epoch', 'epoch')]:
                if trace[actual][pid] != row[expected]:
                    errors.append(f'逐包诊断不符 {pid}/{index + 1}/{actual}')
            if not row['gate_local_us'] or row['generated_local_us'] < row['gate_local_us']:
                errors.append(f'本地时间错误 {pid}/{index + 1}')
        for pid, expected in predicted['packets'].items():
            if expected['records']:
                if pid not in reports or reports[pid]['records'] != expected['records']:
                    errors.append(f'导出与独立重放不符 {pid}')
            elif pid in reports:
                errors.append(f'额外报告 {pid}')
        for index, state in enumerate(states):
            for gate, value in enumerate(state['last_timestamp']):
                expected = predicted['states'][index].get(gate, 0)
                if value != expected or state['has_sample'][gate] != int(gate in predicted['states'][index]):
                    errors.append(f'成功门控状态错误 {index + 1}/{gate}')
        (output / 'observations.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in predicted['observations']))
    return {'status': 'passed' if not errors else 'failed', 'errors': errors,
            'input_packets_including_probes': len(packets), 'received_packets': len(received),
            'workload_samples_per_node': counts, 'report_packets_including_probes': len(reports),
            'predicted_cost': predicted['cost'] if predicted else None}


def run_case(output, control, source, destination, collector, name, config, workload, expected, special, diagnostics):
    directory = output / name
    directory.mkdir()
    handles, packets = [], []
    for switch in control.switches[:-1]:
        names = ['last_timestamp', 'has_sample'] + (TRACE_NAMES if diagnostics else [])
        control.command(switch, [f'register_reset TTEgress.{key}' for key in names], 'cold_start_reset')

    def transmit(rows, cfg, phase):
        for p in rows:
            p['configuration'] = json.loads(json.dumps(cfg))
        send(source, directory, rows, phase)
        packets.extend(rows)

    try:
        handles = [capture(destination, directory, 'destination', True), capture(collector, directory, 'collector', False)]
        probe_config = configuration(thresholds=[1] * 3, capacities=[3] * 3, epoch=0)
        control.apply(probe_config, 'calibration_installation')
        transmit([packet(3000 + i, flow=3, offset_ms=i * 20) for i in range(8)], probe_config, 'calibration-start')
        control.apply(config)
        transmit(workload, config, 'workload')
        extra = {'configuration': config}
        if special == 'stop':
            continuation = [packet(25 + i, offset_ms=i * 200) for i in range(12)]
            transmit(continuation, config, 'after-stop')
            extra['stopped_flow'] = 2
        if special == 'update':
            before = control.state()
            changed = json.loads(json.dumps(config))
            changed['masks']['1'], changed['epochs']['1'] = 2, 11
            control.authorize(1, 2, 11, 'online_update')
            after = control.state()
            extra['state_preserved_on_authorization_update'] = before == after
            transmit([packet(5 + i, offset_ms=i * 200) for i in range(4)], changed, 'after-update')
            idle = control.state()
            extra['source_gate_unchanged_while_unauthorized'] = all(idle[0][key][1] == after[0][key][1] for key in ['last_timestamp', 'has_sample'])
            restored = json.loads(json.dumps(config))
            restored['epochs']['1'] = 12
            control.authorize(1, 1, 12, 'online_update')
            transmit([packet(9)], restored, 'after-restore')
        time.sleep(0.25)
        control.apply(probe_config, 'calibration_installation')
        transmit([packet(3008 + i, flow=3, offset_ms=i * 20) for i in range(8)], probe_config, 'calibration-end')
        time.sleep(0.12)
        for handle in handles:
            finish_capture(handle)
        handles = []
        if diagnostics:
            before = time.time_ns()
            traces = [read_registers(switch, TRACE_NAMES) for switch in control.switches[:-1]]
            states = control.state()
            control.events.append({'node': 0, 'kind': 'diagnostic_read', 'before_ns': before,
                                   'after_ns': time.time_ns(), 'commands': ['read per-packet diagnostics']})
            (directory / 'registers.json').write_text(json.dumps({'traces': traces, 'states': states}) + '\n')
        else:
            traces, states = None, None
        result = verify(directory, packets, traces, states, expected)
        result.update(name=name, **extra)
        if special == 'update' and not all(extra[key] for key in ['state_preserved_on_authorization_update', 'source_gate_unchanged_while_unauthorized']):
            result['errors'].append('权限更新或拒绝重置了门控状态')
            result['status'] = 'failed'
        return result
    finally:
        for handle in handles:
            try:
                finish_capture(handle)
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ordinary', action='store_true')
    parser.add_argument('--cases', nargs='+')
    args = parser.parse_args()
    if os.geteuid():
        parser.error('需要 sudo 运行')
    setLogLevel('warning')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'run_id': output.name, 'status': 'running', 'start_utc': datetime.now(timezone.utc).isoformat(),
                'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
                'diagnostics_enabled': not args.ordinary, 'seed': 0, 'hops': 3, 'sink_nodes': 1,
                'header_bytes': 8, 'record_bytes': 20, 'warmup': '年龄检查保留实际前史，规则正确性不排除首包',
                'scope': '单次确定性基线执行验证，非统计性能或硬件评价',
                'versions': {'p4c': software_version(['p4c-bm2-ss', '--version']), 'bmv2': software_version(['simple_switch', '--version']), 'python': sys.version.split()[0], 'mininet': software_version(['mn', '--version'])},
                'start_load_average': list(os.getloadavg()), 'cases': []}
    manifest_path = output / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    network, rpc_handle, control = None, None, None
    try:
        base_port = 26000 + (os.getpid() % 1000) * 10
        ready, stop = output / 'rpc.ready', output / 'rpc.stop'
        log = open(output / 'rpc.log', 'w')
        rpc = subprocess.Popen([sys.executable, str(ROOT / 'experiments/capture_rpc.py'),
                                '--events', str(output / 'rpc-events.jsonl'), '--pcap', str(output / 'rpc.pcap'),
                                '--ready', str(ready), '--stop', str(stop), '--ports',
                                *[str(base_port + i) for i in range(1, 5)]], stdout=log, stderr=subprocess.STDOUT)
        rpc_handle = (rpc, stop, log)
        deadline = time.monotonic() + 5
        while not ready.exists():
            if rpc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('本机控制抓包未启动')
            time.sleep(0.02)
        network, source, destination, collector, switches = create_chain(
            build(not args.ordinary), output, 3, f'bv{os.getpid() % 65536:04x}', base_port, os.getpid() * 10)
        configure(switches, 3)
        for switch in switches[:-1]:
            switch.cli(['table_add TTIngress.flow_map assign_flow 10.0.0.1 10.0.0.2 17 10303 9000 => 3'])
        control = Control(switches, output)
        selected = cases()
        if args.cases:
            if set(args.cases) - {case[0] for case in selected}:
                raise ValueError('检查名称不合法')
            selected = [case for case in selected if case[0] in args.cases]
        for case in selected:
            started = time.monotonic()
            result = run_case(output, control, source, destination, collector, *case, not args.ordinary)
            result['wall_seconds'] = time.monotonic() - started
            manifest['cases'].append(result)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
            print(case[0], result['status'], result['workload_samples_per_node'], result['errors'][:5], flush=True)
        manifest['status'] = 'passed' if all(case['status'] == 'passed' for case in manifest['cases']) else 'failed'
    except Exception as exc:
        manifest.update(status='failed', failure=str(exc))
        (output / 'failure.txt').write_text(traceback.format_exc())
        print('运行失败', exc, flush=True)
    finally:
        if control:
            (output / 'control-events.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in control.events))
        if network:
            network.stop()
        if rpc_handle:
            finish_capture(rpc_handle)
        manifest['finish_utc'] = datetime.now(timezone.utc).isoformat()
        manifest['finish_load_average'] = list(os.getloadavg())
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return 0 if manifest['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
