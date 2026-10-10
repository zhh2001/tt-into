#!/usr/bin/env python3
"""把冻结的三对象分配参照接到实收报告和 BMv2，独立改变导出链路。"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import traceback

from mininet.log import setLogLevel
from scapy.all import Ether, IP, UDP, Raw
from run_fork_validation import ROOT, SOURCES as FORK_SOURCES, TRACE_NAMES, configure, setup, dump, wait_sender, software_version
from fork_topology import create_fork
from build_strong import build
from run_strong_validation import capture
from run_validation import finish_capture, load_events
from collector.codec import decode_frame as decode_plain
from collector.strong_codec import STATE_TYPE
from control.metered import MeteredClient
from orchestration.deployment import KEYS, parameters
from orchestration.live_assignment import LiveAssignment, ReceivedState

FAMILIES = ('tt', 'dinto', 'intopp', 'intopp_ahead')
SOURCES = list(dict.fromkeys(FORK_SOURCES + ['experiments/run_assignment_validation.py',
    'experiments/admission_io.py', 'src/control/metered.py', 'src/orchestration/live_assignment.py',
    'src/orchestration/assignment_reference.py', 'src/orchestration/slot_reference.py',
    'src/orchestration/deployment.py']))
SCENARIOS = {'normal': ['delay', '0ms'],
             'delay': ['delay', '100ms', '40ms', 'distribution', 'uniform'],
             'loss': ['loss', 'random', '15%']}


def make_packets(workload, base):
    packets = []
    for row in workload['packets']:
        pid, flow = base + row['packet_id'], row['flow']
        payload = pid.to_bytes(4, 'big') + bytes([flow]) * 88
        frame = bytes(Ether(src='02:00:00:00:00:01', dst='02:00:00:00:00:02') /
                      IP(src='10.0.0.1', dst='10.0.0.2', id=pid, ttl=64) /
                      UDP(sport=10100 + flow, dport=9000) / Raw(payload))
        packets.append({'packet_id': pid, 'flow': flow, 'path': row['path'], 'ip_length': 120,
                        'offset_ns': (row['gate_us'][0] + 800000) * 1000,
                        'payload_hex': payload.hex(), 'frame_hex': frame.hex()})
    if len(packets) >= 100 or packets[-1]['packet_id'] >= 4096:
        raise ValueError('诊断标识范围或每案例间距不足')
    return packets


def netem(switch, scenario, directory, label):
    iface = switch.intfs[3].name
    command = ['tc', 'qdisc', 'replace', 'dev', iface, 'root', 'netem', 'limit', '4096', *SCENARIOS[scenario]]
    result = switch.popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = result.communicate(timeout=5)
    if result.returncode:
        raise RuntimeError('导出链路设置失败 ' + stderr.decode())
    stats = switch.popen(['tc', '-s', '-j', 'qdisc', 'show', 'dev', iface], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    out, err = stats.communicate(timeout=5)
    if stats.returncode:
        raise RuntimeError(err.decode())
    dump(directory / ('netem-' + label + '.json'), {'command': command,
         'stdout': stdout.decode(), 'stderr': stderr.decode(), 'statistics': json.loads(out)})


def verify(directory, packets, target, capacity, clients, diagnostics):
    destination = load_events(directory / 'destination.jsonl')
    business = [r for r in destination if r.get('kind') == 'frame' and r.get('src') == '10.0.0.1']
    reports = [r for r in load_events(directory / 'collector.jsonl') if r.get('ether_type') == STATE_TYPE]
    got, received = {r['packet_id']: r for r in business}, {r['packet_id']: r for r in reports}
    applications = {r['payload_hex'] for r in destination if r.get('kind') == 'udp_application'}
    errors, observations = [], []
    if len(got) != len(packets) or len(business) != len(packets) or len(received) != len(reports):
        errors.append('业务完整性或报告唯一性错误')
    for packet in packets:
        pid, row = packet['packet_id'], got.get(packet['packet_id'])
        original = decode_plain(bytes.fromhex(packet['frame_hex']))
        if not row or row['ip_length'] != 120 or row['ttl'] != 63 - len(packet['path']) or not row['ip_checksum_ok'] or row['transport_hex'] != original['transport_hex'] or packet['payload_hex'] not in applications:
            errors.append('业务恢复错误 ' + str(pid))
        selected = [node for node in reversed(packet['path']) if target[node, packet['flow']] is not None]
        report = received.get(pid)
        if report and (report['version'] != 5 or not report['ip_checksum_ok'] or not 1 <= report['count'] <= capacity or report['ip_length'] != 24 + 12 * report['count'] or [r['switch_id'] for r in report['records']] != selected):
            errors.append('报告格式或选择错误 ' + str(pid))
    if diagnostics:
        traces = [{name: c.read_all('TTEgress.' + name) for name in TRACE_NAMES} for c in clients[:3]]
        gates = {}
        for packet in packets:
            count = 0
            for node in packet['path']:
                trace, idx, key = traces[node - 1], packet['packet_id'] & 4095, (node, packet['flow'])
                now, threshold = trace['trace_arrival'][idx], target[key] or 1
                if not now:
                    errors.append('未见合法包')
                if key in gates and (now - gates[key]) % (1 << 48) < threshold:
                    decision = 2
                elif count >= (capacity if target[key] is not None else 0):
                    decision = 3
                else:
                    decision = 1
                    gates[key] = now
                if decision != trace['trace_decision'][idx] or count != trace['trace_count_before'][idx]:
                    errors.append('门控或容量核对错误 ' + str((packet['packet_id'], node)))
                if decision == 1:
                    count += 1
                observations.append({'packet_id': packet['packet_id'], 'node': node,
                                     **{name: trace[name][idx] for name in TRACE_NAMES}})
            report = received.get(packet['packet_id'])
            if report:
                for item in report['records']:
                    trace = traces[item['switch_id'] - 1]
                    idx = packet['packet_id'] & 4095
                    if item['generated_us'] != trace['trace_generated'][idx] or item['queue_packets'] != trace['trace_value'][idx]:
                        errors.append('已收报告状态与诊断不符')
            for node in set((1, 2, 3)) - set(packet['path']):
                if traces[node - 1]['trace_arrival'][packet['packet_id'] & 4095]:
                    errors.append('非法分支被访问')
        for node, client in enumerate(clients[:3], 1):
            for flow in (1, 2, 3):
                if client.read('TTEgress.last_timestamp', flow, kind='diagnostic_read') != gates.get((node, flow), 0) or client.read('TTEgress.has_sample', flow, kind='diagnostic_read') != int((node, flow) in gates):
                    errors.append('最终成功状态错误')
        (directory / 'observations.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in observations))
    if any(r.get('socket_drop_counter', 0) for r in destination + reports):
        errors.append('接收抓包套接字丢包')
    return {'status': 'passed' if not errors else 'failed', 'errors': errors,
            'packet_count': len(packets), 'report_count': len(reports),
            'received_record_counts': [sum(r['switch_id'] == n for p in reports for r in p['records']) for n in (1, 2, 3)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ordinary', action='store_true')
    parser.add_argument('--families', choices=FAMILIES, nargs='+', default=list(FAMILIES))
    parser.add_argument('--reference', type=Path, default=ROOT / 'data/assignment-reference/causal-slots-06')
    parser.add_argument('--steady-build', type=Path, default=ROOT / 'build/bmv2-steady-01')
    args = parser.parse_args()
    if os.geteuid():
        parser.error('需要 sudo 运行')
    setLogLevel('warning')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    selected = json.loads((args.reference / 'selected-parameters.json').read_text())
    workloads = json.loads(gzip.decompress((args.reference / 'workloads.json.gz').read_bytes()))
    workload = next(r for r in workloads if r['split'] == 'heldout' and r['scenario'] == 'jitter' and r['seed'] == 300)
    dump(out / 'frozen-parameters.json', selected)
    dump(out / 'source-workload.json', workload)
    binary = args.steady_build.resolve() / 'install/bin/simple_switch'
    binary_digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if binary_digest != json.loads((args.steady_build / 'manifest.json').read_text())['binary_sha256']:
        raise RuntimeError('隔离交换机摘要错误')
    launch = out / 'launch'
    launch.mkdir()
    wrapper = launch / 'simple_switch'
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(binary)) + ' "$@"\n')
    wrapper.chmod(0o755)
    os.environ['PATH'] = str(launch) + ':' + os.environ['PATH']
    program = build('prefix', not args.ordinary)
    budget = {'scope': '宽松正确性预算，费用含预热，不作等预算性能推断',
              'per_case_inband_per_link_bytes': [1024, 1024, 2048], 'per_case_export_padded_bytes': 5000,
              'whole_run_control_per_node_direction_bytes': 134217728}
    dump(out / 'declared-budget.json', budget)
    manifest = {'run_id': out.name, 'status': 'running', 'start_utc': datetime.now(timezone.utc).isoformat(),
        'scope': '冻结配置的软件正确性验证，独立导出链路损伤，无统计性能结论',
        'diagnostics': not args.ordinary, 'families': args.families, 'seed': 300,
        'netem_seed': None, 'netem_randomness': '本机 tc 6.1 不支持 seed，保存实际丢失与延迟，不视为配对随机重复',
        'source_sha256': {s: hashlib.sha256((ROOT / s).read_bytes()).hexdigest() for s in SOURCES},
        'input_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                         (out / 'frozen-parameters.json', out / 'source-workload.json', out / 'declared-budget.json')},
        'p4_json_sha256': hashlib.sha256(program.read_bytes()).hexdigest(), 'steady_binary_sha256': binary_digest,
        'software': {'bmv2': software_version([str(binary), '--version']), 'p4c': software_version(['p4c-bm2-ss', '--version']),
                     'python': sys.version, 'mininet': software_version(['mn', '--version']), 'tc': software_version(['tc', '-V'])},
        'host': {'cpu_count': os.cpu_count(), 'load_start': list(os.getloadavg())}, 'cases': [],
        'requirements_window_offset_us': [[800000, 3200000]], 'deadlines_us': [[220000, 220000, 260000]],
        'warmup_us': [0, 800000], 'slot_us': 100000, 'format': 'prefix 4+12 B',
        'clock_input': '在线只用已收报告和已发生的本机发送前日志界；固定偏移区间，非可部署同步协议',
        'effect': '初次容量启用在业务开始前完成，后续目标变化立即失败，不测试跨节点动态原子更新',
        'control': '逐 API 序列化和全程 loopback 帧分别保存，文件仪表与求解为主机计算',
        'declared_budget': budget}
    for name in SOURCES:
        target = out / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    dump(out / 'manifest.json', manifest)
    network, clients, handles, rpc_handle, sender = None, [], [], None, None
    rpc_log = []
    started = time.monotonic()
    try:
        ready, stop = out / 'rpc.ready', out / 'rpc.stop'
        log = (out / 'rpc-capture.log').open('w')
        process = subprocess.Popen([sys.executable, str(ROOT / 'experiments/strong_rpc.py'),
            '--events', str(out / 'rpc.jsonl'), '--pcap', str(out / 'rpc.pcap'), '--ready', str(ready),
            '--stop', str(stop), '--ports', '9471', '9472', '9473', '9474'], stdout=log, stderr=subprocess.STDOUT)
        rpc_handle = process, stop, log
        timeout = time.monotonic() + 5
        while not ready.exists():
            if process.poll() is not None or time.monotonic() >= timeout:
                raise RuntimeError('控制捕获未启动')
            time.sleep(.01)
        network, source, destination, collector, switches = create_fork(program, out)
        configure(switches)
        clients = [MeteredClient(s.thrift_port, n, rpc_log) for n, s in enumerate(switches, 1)]
        case_number = 0
        for capacity in (1, 2):
            for scenario in SCENARIOS:
                for family in args.families:
                    name = f'k{capacity}-{scenario}-{family}'
                    directory = out / name
                    directory.mkdir()
                    params = next(r['parameters'] for r in selected if r['capacity'] == capacity and r['family'] == family)
                    controller, state = LiveAssignment(family, params, capacity), ReceivedState()
                    cold = controller.decide((220000, 220000, 260000), state.snapshot(time.monotonic_ns()))
                    target = parameters(cold['thresholds'])
                    packets = make_packets(workload, case_number * 100)
                    dump(directory / 'packets.json', packets)
                    dump(directory / 'barriers.json', [])
                    netem(switches[3], scenario, directory, 'before')
                    install_begin = len(rpc_log)
                    setup(clients, 'flow', 1, 0)
                    # setup 已给所有逐流索引阈值 1，不重复计五次写入。
                    install_end = len(rpc_log)
                    if not args.ordinary:
                        for c in clients[:3]:
                            for tr in TRACE_NAMES:
                                c.reset('TTEgress.' + tr)
                    activation_begin = len(rpc_log)
                    for (node, flow), value in target.items():
                        if value is not None:
                            clients[node - 1].write('TTEgress.max_count', flow, capacity, kind='initial_activation')
                    activated = time.monotonic_ns()
                    activation_end = len(rpc_log)
                    handles = [capture(destination, directory, 'destination', True), capture(collector, directory, 'collector')]
                    origin = time.monotonic_ns() + 600000000
                    case = {'name': name, 'capacity': capacity, 'scenario': scenario, 'family': family, 'parameters': params,
                            'origin_monotonic_ns': origin, 'installation_rpc_indices': [install_begin, install_end],
                            'activation_rpc_indices': [activation_begin, activation_end], 'activated_monotonic_ns': activated,
                            'target': cold['thresholds'], 'cold_decision': cold}
                    dump(directory / 'result.json', dict(case, status='running'))
                    sender = source.popen([sys.executable, str(ROOT / 'experiments/admission_io.py'), '--ifaces',
                        *[source.intfs[i].name for i in range(3)], '--packets', str(directory / 'packets.json'),
                        '--events', str(directory / 'send.jsonl'), '--barriers', str(directory / 'barriers.json'),
                        '--directory', str(directory), '--start-ns', str(origin)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    with (directory / 'decisions.jsonl').open('w') as decisions:
                        for offset_us in range(100000, 3200000, 100000):
                            due = origin + offset_us * 1000
                            while (left := due - time.monotonic_ns()) > 0:
                                time.sleep(min(left / 1e9, .001))
                            began = time.monotonic_ns()
                            reports = [r for r in load_events(directory / 'collector.jsonl') if r.get('ether_type') == STATE_TYPE]
                            sends = {r['packet_id']: r for r in load_events(directory / 'send.jsonl')}
                            cutoff = time.monotonic_ns()
                            state.ingest(reports, sends, cutoff)
                            observed = state.snapshot(cutoff)
                            action = controller.decide((220000, 220000, 260000), observed)
                            if parameters(action['thresholds']) != target:
                                raise RuntimeError('冻结配置产生在线变化，当前检查不支持在途更新')
                            finished = time.monotonic_ns()
                            decisions.write(json.dumps({'scheduled_monotonic_ns': due, 'began_monotonic_ns': began,
                                'cutoff_monotonic_ns': cutoff, 'finished_monotonic_ns': finished,
                                'observed_packet_ids': sorted(state.seen), 'observations': observed,
                                'action': action, 'writes': [], 'extra_network_polling_calls': 0}) + '\n')
                            decisions.flush()
                    wait_sender(sender)
                    sender = None
                    # 保留测量终点后的送达，不把它们提前算作窗口内可用。
                    while time.monotonic_ns() < origin + 3500000000:
                        time.sleep(.005)
                    for handle in handles:
                        finish_capture(handle)
                    handles = []
                    stats = switches[3].popen(['tc', '-s', '-j', 'qdisc', 'show', 'dev', switches[3].intfs[3].name],
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    stdout, stderr = stats.communicate(timeout=5)
                    if stats.returncode:
                        raise RuntimeError(stderr.decode())
                    dump(directory / 'netem-after.json', json.loads(stdout))
                    case.update(verify(directory, packets, target, capacity, clients, not args.ordinary))
                    dump(directory / 'result.json', case)
                    manifest['cases'].append(case)
                    dump(out / 'manifest.json', manifest)
                    if case['status'] != 'passed':
                        raise RuntimeError('独立执行核对失败 ' + str(case['errors'][:5]))
                    print(name, 'reports', case['report_count'], 'records', case['received_record_counts'], 'passed', flush=True)
                    case_number += 1
        manifest['status'] = 'passed'
    except Exception as exc:
        manifest.update(status='failed', error=str(exc), traceback=traceback.format_exc())
        print(traceback.format_exc(), file=sys.stderr)
    finally:
        if sender and sender.poll() is None:
            sender.terminate()
            sender.wait(timeout=3)
        for handle in handles:
            finish_capture(handle)
        for client in clients:
            client.close()
        if network:
            network.stop()
        if rpc_handle:
            finish_capture(rpc_handle)
        (out / 'rpc-operations.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rpc_log))
        manifest.update(end_utc=datetime.now(timezone.utc).isoformat(), duration_seconds=time.monotonic() - started)
        manifest['host']['load_end'] = list(os.getloadavg())
        dump(out / 'manifest.json', manifest)
    return int(manifest['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
