#!/usr/bin/env python3
"""固定分支资格及 TT-INT 预声明调阈值的真实执行核对，不做性能排名。"""
import argparse
from datetime import datetime, timezone
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

from scapy.all import Ether, IP, Raw, UDP
from mininet.log import setLogLevel
from build_strong import ROOT, build
from fork_topology import create_fork
from run_strong_validation import capture
from run_validation import finish_capture, load_events, software_version
from control.metered import MeteredClient
from collector.codec import decode_frame as decode_plain
from collector.strong_codec import STATE_TYPE
from orchestration.path_reference import fork_packets

SOURCES = ['experiments/run_fork_validation.py', 'experiments/fork_topology.py', 'experiments/fork_io.py',
           'experiments/topology.py', 'experiments/build_strong.py', 'experiments/run_strong_validation.py',
           'experiments/run_validation.py', 'experiments/build.py', 'experiments/strong_io.py',
           'experiments/strong_rpc.py', 'experiments/packet_io.py', 'src/p4/strong_int.p4',
           'src/control/bmv2.py', 'src/control/metered.py', 'src/collector/strong_codec.py',
           'src/collector/codec.py', 'src/orchestration/path_reference.py', 'src/orchestration/strong.py',
           'src/orchestration/model.py', 'src/orchestration/budget_model.py', 'src/orchestration/metrics.py']
TRACE_NAMES = ['trace_arrival', 'trace_generated', 'trace_value', 'trace_decision', 'trace_count_before']


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def configure(switches):
    for node, switch in enumerate(switches, 1):
        switch.cli([f'table_set_default TTIngress.node_config set_node {node} {int(node < 4)} {int(node == 4)} 1500',
                    f'table_add TTIngress.ipv4_forward forward 10.0.0.2 => 2 02:00:00:00:00:02 02:00:00:10:00:{node:02x}',
                    *[f'table_add TTIngress.flow_map assign_flow 10.0.0.1 10.0.0.2 17 {10100 + flow} 9000 => {flow}' for flow in (1, 2, 3)],
                    *(['mirroring_add 100 3'] if node == 4 else [])])


def setup(clients, scope, threshold, capacity=1):
    for node, client in enumerate(clients[:3], 1):
        for reg in ['last_timestamp', 'has_sample', 'force_sampling']:
            client.reset('TTEgress.' + reg)
        for reg in ['packet_count', 'arrival_valid', 'arrival_last', 'arrival_dev']:
            client.reset('TTIngress.' + reg)
        client.default('TTEgress.gate_policy', 'TTEgress.choose_scope', [(int(scope == 'port'), 1)])
        client.default('TTEgress.selection_policy', 'TTEgress.select_mode', [(0, 8), (1, 8)])
        for flow in (1, 2, 3):
            client.write('TTEgress.max_count', flow, capacity)
            client.write('TTEgress.time_threshold', 258 if scope == 'port' else flow, threshold if node < 3 else 1)
            client.write('TTIngress.ideal_interval', flow, 100000 if flow < 3 else 200000)
            # 合流节点只为第三条流首次封装，不能把分支中被 1:N 跳过的包重新激活。
            client.entry('TTIngress.sampling_policy', flow, 32, 'TTIngress.sampling',
                         [(int(node < 3 or flow == 3), 1), (1, 32)])
    clients[3].write('TTEgress.drop_report', 0, 0)


def make_packets(base_id):
    result = []
    for p in fork_packets():
        pid = base_id + p.packet_id
        payload = pid.to_bytes(4, 'big') + bytes([p.flow]) * 88
        frame = bytes(Ether(src='02:00:00:00:00:01', dst='02:00:00:00:00:02') /
                      IP(src='10.0.0.1', dst='10.0.0.2', ttl=64, id=pid) /
                      UDP(sport=10100 + p.flow, dport=9000) / Raw(payload))
        result.append({'packet_id': pid, 'flow': p.flow, 'path': list(p.path), 'links': list(p.links),
                       'offset_ns': int((p.gate_us[0] + 800000) * 1000), 'ip_length': 120,
                       'frame_hex': frame.hex(), 'payload_hex': payload.hex()})
    return result


def sender(source, directory, packets, name, origin, pause=False):
    path = directory / (name + '-packets.json')
    dump(path, packets)
    command = [sys.executable, str(ROOT / 'experiments/fork_io.py'), '--ifaces',
               *[source.intfs[i].name for i in range(3)], '--packets', str(path),
               '--events', str(directory / (name + '-send.jsonl')), '--start-ns', str(origin)]
    if pause:
        command += ['--pause-offset-ns', '2000000000', '--pause-ready', str(directory / 'sender-paused.json'),
                    '--resume', str(directory / 'sender-resume')]
    process = source.popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if pause:
        return process
    wait_sender(process)


def wait_sender(process):
    output, error = process.communicate(timeout=10)
    if process.returncode:
        raise RuntimeError('发包失败 ' + error.decode()[-1000:])


def wait_reports(directory, expected):
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        # 只读取采集器已经收到的报告，用作正确性运行中的在途排空屏障。
        rows = [r for r in load_events(directory / 'collector.jsonl') if r.get('ether_type') == STATE_TYPE]
        if {r['packet_id'] for r in rows} >= expected:
            return time.monotonic_ns()
        time.sleep(0.001)
    raise RuntimeError('更新前的业务报告未及时全部排空')


def verify(directory, packets, scope, initial_threshold, dynamic, clients, diagnostics, capacity=1):
    received = [r for r in load_events(directory / 'destination.jsonl') if r.get('kind') == 'frame' and r.get('src') == '10.0.0.1']
    reports = [r for r in load_events(directory / 'collector.jsonl') if r.get('ether_type') == STATE_TYPE]
    got, exported = {r['packet_id']: r for r in received}, {r['packet_id']: r for r in reports}
    apps = [r['payload_hex'] for r in load_events(directory / 'destination.jsonl') if r.get('kind') == 'udp_application']
    errors, observations = [], []
    if len(received) != len(packets) or len(got) != len(packets) or len(exported) != len(reports):
        errors.append('业务数量或报告唯一性错误')
    for p in packets:
        original = decode_plain(bytes.fromhex(p['frame_hex']))
        row = got.get(p['packet_id'])
        if not row or row['transport_hex'] != original['transport_hex'] or row['ttl'] != 64 - len(p['path']) - 1 or row['ip_length'] != 120 or not row['ip_checksum_ok'] or p['payload_hex'] not in apps:
            errors.append('业务恢复错误 ' + str(p['packet_id']))
        report = exported.get(p['packet_id'])
        if report:
            if report['version'] != 5 or not report['ip_checksum_ok'] or report['ip_length'] != 24 + 12 * report['count'] or not 1 <= report['count'] <= capacity:
                errors.append('报告格式错误 ' + str(p['packet_id']))
            if any(r['switch_id'] not in p['path'] or r['hop'] != p['path'].index(r['switch_id']) + 1 for r in report['records']):
                errors.append('报告超出物理路径资格 ' + str(p['packet_id']))
    traces = []
    if diagnostics:
        for client in clients[:3]:
            traces.append({name: client.read_all('TTEgress.' + name) for name in TRACE_NAMES})
        gates = {}
        for p in packets:
            records, hops = [], []
            for node in p['path']:
                idx, trace = p['packet_id'] & 4095, traces[node - 1]
                now = trace['trace_arrival'][idx]
                if not now:
                    errors.append('节点未看到合法包 ' + str(p['packet_id']))
                threshold = 1 if node == 3 else (300001 if dynamic and p['offset_ns'] >= 2000000000 else initial_threshold)
                key = (node, 0 if scope == 'port' else p['flow'])
                if key in gates and (now - gates[key]) % (1 << 48) < threshold:
                    decision = 2
                elif len(records) >= capacity:
                    decision = 3
                else:
                    decision = 1
                    gates[key] = now
                    records.insert(0, node)
                if decision != trace['trace_decision'][idx] or trace['trace_count_before'][idx] != len(hops):
                    errors.append(f'门控或容量决定错误 {p["packet_id"]}/{node}')
                if decision == 1:
                    hops = records.copy()
                observations.append({'packet_id': p['packet_id'], 'node': node,
                                     **{name: trace[name][idx] for name in TRACE_NAMES}})
            report = exported.get(p['packet_id'])
            if (report is None) != (not records) or report and [r['switch_id'] for r in report['records']] != records:
                errors.append('最终记录与执行不一致 ' + str(p['packet_id']))
            if report:
                for item in report['records']:
                    trace = traces[item['switch_id'] - 1]
                    if item['generated_us'] != trace['trace_generated'][p['packet_id'] & 4095] or item['queue_packets'] != trace['trace_value'][p['packet_id'] & 4095]:
                        errors.append('队列状态或生成时间不一致 ' + str(p['packet_id']))
        (directory / 'observations.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in observations))
        # 未经过分支的报文不能产生该分支上的执行状态。
        for p in packets:
            for node in set((1, 2, 3)) - set(p['path']):
                if traces[node - 1]['trace_arrival'][p['packet_id'] & 4095]:
                    errors.append('报文进入了错误分支')
    return {'status': 'passed' if not errors else 'failed', 'errors': errors,
            'packet_count': len(packets), 'report_count': len(reports),
            'record_counts': [sum(r['switch_id'] == node for report in reports for r in report['records']) for node in (1, 2, 3)]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--steady-build', type=Path, default=ROOT / 'build/bmv2-steady-01')
    p.add_argument('--ordinary', action='store_true')
    p.add_argument('--budget-file', type=Path, default=ROOT / 'experiments/configs/fork-correctness-budget.json')
    a = p.parse_args()
    if os.geteuid() != 0:
        p.error('需要 sudo 运行 Mininet，不在参数中传密码')
    setLogLevel('warning')
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    declared_budget = json.loads(a.budget_file.read_text())
    shutil.copyfile(a.budget_file, out / 'declared-budget.json')
    build_info = json.loads((a.steady_build / 'manifest.json').read_text())
    binary = a.steady_build.resolve() / 'install/bin/simple_switch'
    actual = hashlib.sha256(binary.read_bytes()).hexdigest()
    if actual != build_info['binary_sha256']:
        raise RuntimeError('隔离交换机摘要不一致')
    launch = out / 'launch'
    launch.mkdir()
    wrapper = launch / 'simple_switch'
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(binary)) + ' "$@"\n')
    wrapper.chmod(0o755)
    os.environ['PATH'] = str(launch) + ':' + os.environ['PATH']
    program = build('prefix', not a.ordinary)
    manifest = {'run_id': out.name, 'start_utc': datetime.now(timezone.utc).isoformat(), 'status': 'running',
                'scope': '软件正确性与成本验证，不是统计性能比较', 'random_seed': None,
                'diagnostics': not a.ordinary, 'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
                'p4_json_sha256': hashlib.sha256(program.read_bytes()).hexdigest(), 'steady_binary_sha256': actual,
                'software': {'bmv2': software_version([str(binary), '--version']), 'p4c': software_version(['p4c-bm2-ss', '--version']),
                             'python': sys.version, 'mininet': software_version(['mn', '--version'])},
                'host': {'cpu_count': os.cpu_count(), 'load_start': list(os.getloadavg())}, 'cases': [],
                'format': 'prefix 4+12 B, 无许可及版本切换字段', 'tasks': [{'node': n, 'port': 2, 'queue_class': 0} for n in (1, 2, 3)],
                'requirements_window_offset_us': [[800000, 2020000], [2020000, 3200000]],
                'deadlines_us': [[150000, 150000, 250000], [650000, 650000, 175000]],
                'warmup_us': [0, 800000], 'update': '预声明调阈值，提前排空在途报告，不重置成功门控',
                'control_cost_basis': 'RPC 序列化及全量 loopback Ethernet 帧分别保存'}
    manifest['declared_budget'] = declared_budget
    manifest['budget_sha256'] = hashlib.sha256(a.budget_file.read_bytes()).hexdigest()
    for name in SOURCES:
        target = out / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    dump(out / 'manifest.json', manifest)
    network, handles, clients, rpc_handle, sending = None, [], [], None, None
    rpc_log = []
    start = time.monotonic()
    try:
        # 控制捕获从启动握手前开始，含初始 CLI、计量 RPC 及诊断读取。
        rpc_ready, rpc_stop = out / 'rpc.ready', out / 'rpc.stop'
        rpc_file = (out / 'rpc-capture.log').open('w')
        rpc_process = subprocess.Popen([sys.executable, str(ROOT / 'experiments/strong_rpc.py'),
                                        '--events', str(out / 'rpc.jsonl'), '--pcap', str(out / 'rpc.pcap'),
                                        '--ready', str(rpc_ready), '--stop', str(rpc_stop),
                                        '--ports', '9471', '9472', '9473', '9474'], stdout=rpc_file, stderr=subprocess.STDOUT)
        rpc_handle = rpc_process, rpc_stop, rpc_file
        deadline = time.monotonic() + 5
        while not rpc_ready.exists():
            if rpc_process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('控制抓包启动失败')
            time.sleep(0.01)
        network, source, destination, collector, switches = create_fork(program, out)
        configure(switches)
        clients = [MeteredClient(s.thrift_port, node, rpc_log) for node, s in enumerate(switches, 1)]
        cases = [('scheduled_tt', 'flow', 1, True, 1), ('static_capacity2', 'flow', 1, False, 2)] if a.ordinary else [
            ('tt_fixed', 'flow', 1, False, 1), ('shared_fixed', 'port', 1, False, 1),
            ('static_compromise', 'flow', 150000, False, 1), ('scheduled_tt', 'flow', 1, True, 1)]
        for number, (name, scope, threshold, dynamic, capacity) in enumerate(cases):
            directory = out / name
            directory.mkdir()
            setup(clients, scope, threshold, capacity)
            if not a.ordinary:
                for client in clients[:3]:
                    for trace_name in TRACE_NAMES:
                        client.reset('TTEgress.' + trace_name)
            packets = make_packets(number * 100)
            dump(directory / 'packets.json', packets)
            handles = [capture(destination, directory, 'destination', True), capture(collector, directory, 'collector')]
            origin = time.monotonic_ns() + 300000000
            case = {'name': name, 'scope': scope, 'initial_threshold_us': threshold,
                    'capacity': capacity,
                    'origin_monotonic_ns': origin, 'dynamic': dynamic, 'rpc_log_begin': len(rpc_log), 'updates': []}
            if dynamic:
                before = [p for p in packets if p['offset_ns'] < 2000000000]
                sending = sender(source, directory, packets, 'main', origin, pause=True)
                deadline = time.monotonic() + 4
                while not (directory / 'sender-paused.json').exists():
                    if sending.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('发送进程未到达配置更新屏障')
                    time.sleep(0.0005)
                case['all_preupdate_reports_received_ns'] = wait_reports(directory, {p['packet_id'] for p in before})
                for node, flow in ((1, 1), (2, 2)):
                    clients[node - 1].write('TTEgress.time_threshold', flow, 300001, kind='online_update')
                    case['updates'].append(rpc_log[-1].copy())
                (directory / 'sender-resume').write_text('resume\n')
                wait_sender(sending)
                sending = None
                all_sends = load_events(directory / 'main-send.jsonl')
                after_ids = {p['packet_id'] for p in packets if p['offset_ns'] >= 2000000000}
                first_after = min(r['send_before_monotonic_ns'] for r in all_sends if r['packet_id'] in after_ids)
                if max(r['after_monotonic_ns'] for r in case['updates']) >= first_after or first_after - origin > 2020000000:
                    raise RuntimeError('更新未在后续业务包之前完成或超过需求生效点')
            else:
                sender(source, directory, packets, 'main', origin)
                all_sends = load_events(directory / 'main-send.jsonl')
            (directory / 'send.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in all_sends))
            while (left := origin + 3200000000 - time.monotonic_ns()) > 0:
                time.sleep(min(left / 1e9, 0.01))
            time.sleep(0.1)
            for handle in handles:
                finish_capture(handle)
            handles = []
            case.update(verify(directory, packets, scope, threshold, dynamic, clients, not a.ordinary, capacity))
            case['rpc_log_end'] = len(rpc_log)
            dump(directory / 'result.json', case)
            manifest['cases'].append(case)
            dump(out / 'manifest.json', manifest)
            if case['status'] != 'passed':
                raise RuntimeError('核对失败 ' + name + ': ' + '; '.join(case['errors'][:5]))
            print(name, case['record_counts'], 'passed', flush=True)
        manifest['status'] = 'passed'
    except Exception as exc:
        manifest.update(status='failed', error=str(exc), traceback=traceback.format_exc())
        print(traceback.format_exc(), file=sys.stderr)
    finally:
        if sending and sending.poll() is None:
            sending.terminate()
            sending.wait(timeout=3)
        for handle in handles:
            finish_capture(handle)
        for client in clients:
            client.close()
        if network:
            network.stop()
        if rpc_handle:
            finish_capture(rpc_handle)
        (out / 'rpc-operations.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rpc_log))
        manifest.update(end_utc=datetime.now(timezone.utc).isoformat(), duration_seconds=time.monotonic() - start)
        manifest['host']['load_end'] = list(os.getloadavg())
        dump(out / 'manifest.json', manifest)
    return int(manifest['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
