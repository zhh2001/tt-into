#!/usr/bin/env python3
"""检查两个预声明 TT-INT 配置的初始化、搭载及实际成本。"""
import argparse
import copy
from dataclasses import asdict
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

from mininet.log import setLogLevel
from run_fork_validation import (ROOT, SOURCES as FORK_SOURCES, TRACE_NAMES, configure, setup,
                                 make_packets, wait_sender, dump, software_version)
from fork_topology import create_fork
from build_strong import build
from run_strong_validation import capture
from run_validation import finish_capture, load_events
from control.metered import MeteredClient
from collector.codec import decode_frame as decode_plain
from collector.strong_codec import STATE_TYPE
from orchestration.deployment import KEYS, activation_plan, parameters, interval_margin

SOURCES = list(dict.fromkeys(FORK_SOURCES + ['experiments/run_admission_validation.py',
    'experiments/admission_io.py', 'experiments/analyze_fork_validation.py',
    'src/orchestration/deployment.py']))


def verify(directory, packets, candidate, stages, clients, diagnostics):
    destination = load_events(directory / 'destination.jsonl')
    business = [r for r in destination if r.get('kind') == 'frame' and r.get('src') == '10.0.0.1']
    reports = [r for r in load_events(directory / 'collector.jsonl') if r.get('ether_type') == STATE_TYPE]
    got, exported = {r['packet_id']: r for r in business}, {r['packet_id']: r for r in reports}
    applications = {r['payload_hex'] for r in destination if r.get('kind') == 'udp_application'}
    errors, observations = [], []
    capacity = candidate['capacity']
    if len(got) != len(packets) or len(business) != len(packets) or len(exported) != len(reports):
        errors.append('业务完整性或报告唯一性错误')
    for p in packets:
        r = got.get(p['packet_id'])
        plain = decode_plain(bytes.fromhex(p['frame_hex']))
        if not r or r['ip_length'] != 120 or r['ttl'] != 63 - len(p['path']) or not r['ip_checksum_ok'] or r['transport_hex'] != plain['transport_hex'] or p['payload_hex'] not in applications:
            errors.append('业务恢复错误 ' + str(p['packet_id']))
        report = exported.get(p['packet_id'])
        if report and (report['version'] != 5 or not report['ip_checksum_ok'] or not 1 <= report['count'] <= capacity or report['ip_length'] != 24 + 12 * report['count']):
            errors.append('导出格式错误 ' + str(p['packet_id']))
        if report and any(r['switch_id'] not in p['path'] or r['hop'] != p['path'].index(r['switch_id']) + 1 for r in report['records']):
            errors.append('采样超出实际路径资格 ' + str(p['packet_id']))
    if diagnostics:
        traces = [{name: c.read_all('TTEgress.' + name) for name in TRACE_NAMES} for c in clients[:3]]
        thresholds = {key: value or 1 for key, value in parameters(candidate['native_best']['early']).items()}
        capacities = dict.fromkeys(KEYS, 0)
        gates, index = {}, 0
        for p in packets:
            while index < len(stages) and p['offset_ns'] >= stages[index]['offset_us'] * 1000:
                for w in stages[index]['writes']:
                    (thresholds if w.register.endswith('time_threshold') else capacities)[w.node, w.flow] = w.value
                index += 1
            selected = []
            for node in p['path']:
                idx, trace, key = p['packet_id'] & 4095, traces[node - 1], (node, p['flow'])
                now = trace['trace_arrival'][idx]
                count_before = len(selected)
                if not now:
                    errors.append('未见合法包 ' + str(p['packet_id']))
                if key in gates and (now - gates[key]) % (1 << 48) < thresholds[key]:
                    decision = 2
                elif len(selected) >= capacities[key]:
                    decision = 3
                else:
                    decision = 1
                    gates[key] = now
                    selected.insert(0, node)
                if decision != trace['trace_decision'][idx] or count_before != trace['trace_count_before'][idx]:
                    errors.append(f'独立门控或容量核对错误 {p["packet_id"]}/{node}')
                observations.append({'packet_id': p['packet_id'], 'node': node,
                                     **{name: trace[name][idx] for name in TRACE_NAMES}})
            report = exported.get(p['packet_id'])
            if bool(selected) != bool(report) or report and [r['switch_id'] for r in report['records']] != selected:
                errors.append('最终选择错误 ' + str(p['packet_id']))
            if report:
                for r in report['records']:
                    tr = traces[r['switch_id'] - 1]
                    idx = p['packet_id'] & 4095
                    if r['generated_us'] != tr['trace_generated'][idx] or r['queue_packets'] != tr['trace_value'][idx]:
                        errors.append('采样值或生成时间错误')
            for node in set((1, 2, 3)) - set(p['path']):
                if traces[node - 1]['trace_arrival'][p['packet_id'] & 4095]:
                    errors.append('非法分支被访问')
        for node, c in enumerate(clients[:3], 1):
            for flow in (1, 2, 3):
                actual = c.read('TTEgress.last_timestamp', flow, kind='diagnostic_read')
                valid = c.read('TTEgress.has_sample', flow, kind='diagnostic_read')
                if actual != gates.get((node, flow), 0) or valid != int((node, flow) in gates):
                    errors.append('最终成功状态不符')
        (directory / 'observations.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in observations))
    if any(r.get('socket_drop_counter', 0) for r in destination + reports):
        errors.append('抓包套接字丢包')
    return {'status': 'passed' if not errors else 'failed', 'errors': errors,
            'packet_count': len(packets), 'report_count': len(reports),
            'record_counts': [sum(r['switch_id'] == n for p in reports for r in p['records']) for n in (1, 2, 3)]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--ordinary', action='store_true')
    p.add_argument('--candidates', type=Path, default=ROOT / 'data/admission-reference/flow-indices-03/results.json')
    p.add_argument('--points', type=int, nargs='+', default=[10, 37])
    p.add_argument('--threshold-margin-us', type=int, default=0)
    p.add_argument('--steady-build', type=Path, default=ROOT / 'build/bmv2-steady-01')
    a = p.parse_args()
    if os.geteuid():
        p.error('需要 sudo 运行')
    setLogLevel('warning')
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    candidate_rows = json.loads(a.candidates.read_text())
    candidates = []
    for index in a.points:
        row = copy.deepcopy(candidate_rows[index])
        if row['native_best']['admitted_weight'] != 5:
            raise ValueError('本正确性运行只接受预声明的全任务候选')
        row['original_native_best'] = copy.deepcopy(row['native_best'])
        for phase in ('early', 'late'):
            row['native_best'][phase] = interval_margin(row['native_best'][phase], a.threshold_margin_us)
        candidates.append({'point_index': index, 'threshold_margin_us': a.threshold_margin_us, **row})
    dump(out / 'candidates.json', candidates)
    budget = {'scope': '点的原有数据预算另行核对，控制采用宽松正确性上限',
              'per_case_inband_per_link_bytes': [1024, 1024, 960],
              'per_case_export_bytes_with_minimum_ethernet_padding': 3600,
              'whole_run_control_per_node_direction_bytes': 8388608}
    dump(out / 'declared-budget.json', budget)
    binary = a.steady_build.resolve() / 'install/bin/simple_switch'
    build_info = json.loads((a.steady_build / 'manifest.json').read_text())
    binary_digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if binary_digest != build_info['binary_sha256']:
        raise RuntimeError('隔离交换机摘要错误')
    launch = out / 'launch'
    launch.mkdir()
    wrapper = launch / 'simple_switch'
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(binary)) + ' "$@"\n')
    wrapper.chmod(0o755)
    os.environ['PATH'] = str(launch) + ':' + os.environ['PATH']
    program = build('prefix', not a.ordinary)
    manifest = {'run_id': out.name, 'start_utc': datetime.now(timezone.utc).isoformat(), 'status': 'running',
                'scope': '软件执行、实际年龄与成本核对，非随机性能试验', 'random_seed': None,
                'diagnostics': not a.ordinary, 'source_sha256': {s: hashlib.sha256((ROOT / s).read_bytes()).hexdigest() for s in SOURCES},
                'p4_json_sha256': hashlib.sha256(program.read_bytes()).hexdigest(), 'steady_binary_sha256': binary_digest,
                'candidate_sha256': hashlib.sha256(a.candidates.read_bytes()).hexdigest(),
                'software': {'bmv2': software_version([str(binary), '--version']), 'p4c': software_version(['p4c-bm2-ss', '--version']),
                             'python': sys.version, 'mininet': software_version(['mn', '--version'])},
                'host': {'cpu_count': os.cpu_count(), 'load_start': list(os.getloadavg())}, 'cases': [],
                'requirements_window_offset_us': [[800000, 2020000], [2020000, 3200000]],
                'deadlines_us': [[150000, 150000, 250000], [650000, 650000, 175000]],
                'warmup_us': [0, 800000], 'format': 'prefix 4+12 B, 固定任务映射，无许可版本',
                'update': '提前 10–20 ms 的空隙排空屏障，保留成功状态，未来需求已知',
                'declared_budget': budget, 'budget_sha256': hashlib.sha256((out / 'declared-budget.json').read_bytes()).hexdigest()}
    for s in SOURCES:
        target = out / 'executed-source' / s
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / s, target)
    dump(out / 'manifest.json', manifest)
    network, handles, clients, rpc_handle, sending = None, [], [], None, None
    rpc_log = []
    start = time.monotonic()
    try:
        ready, stop = out / 'rpc.ready', out / 'rpc.stop'
        log = (out / 'rpc-capture.log').open('w')
        process = subprocess.Popen([sys.executable, str(ROOT / 'experiments/strong_rpc.py'),
                                    '--events', str(out / 'rpc.jsonl'), '--pcap', str(out / 'rpc.pcap'),
                                    '--ready', str(ready), '--stop', str(stop), '--ports', '9471', '9472', '9473', '9474'],
                                   stdout=log, stderr=subprocess.STDOUT)
        rpc_handle = process, stop, log
        timeout = time.monotonic() + 5
        while not ready.exists():
            if process.poll() is not None or time.monotonic() >= timeout:
                raise RuntimeError('控制捕获未启动')
            time.sleep(0.01)
        network, source, destination, collector, switches = create_fork(program, out)
        configure(switches)
        clients = [MeteredClient(s.thrift_port, n, rpc_log) for n, s in enumerate(switches, 1)]
        for number, candidate in enumerate(candidates):
            name = 'point-' + str(candidate['point_index'])
            directory = out / name
            directory.mkdir()
            install_begin = len(rpc_log)
            setup(clients, 'flow', 1, 0)
            early = candidate['native_best']['early']
            for (node, flow), value in parameters(early).items():
                clients[node - 1].write('TTEgress.time_threshold', flow, value or 1)
            if not a.ordinary:
                for c in clients[:3]:
                    for tr in TRACE_NAMES:
                        c.reset('TTEgress.' + tr)
            install_end = len(rpc_log)
            stages = activation_plan(early, candidate['native_best']['late'], candidate['capacity'])
            dump(directory / 'barriers.json', [s['offset_us'] for s in stages])
            packets = make_packets(number * 100)
            dump(directory / 'packets.json', packets)
            handles = [capture(destination, directory, 'destination', True), capture(collector, directory, 'collector')]
            origin = time.monotonic_ns() + 600000000
            case = {'name': name, 'dynamic': True, 'capacity': candidate['capacity'], 'candidate': candidate,
                    'origin_monotonic_ns': origin, 'installation_rpc_indices': [install_begin, install_end], 'updates': []}
            sending = source.popen([sys.executable, str(ROOT / 'experiments/admission_io.py'), '--ifaces',
                                    *[source.intfs[i].name for i in range(3)], '--packets', str(directory / 'packets.json'),
                                    '--events', str(directory / 'send.jsonl'), '--barriers', str(directory / 'barriers.json'),
                                    '--directory', str(directory), '--start-ns', str(origin)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for index, stage in enumerate(stages):
                timeout = time.monotonic() + 4
                ready = directory / f'barrier-{index}.ready'
                while not ready.exists():
                    if sending.poll() is not None or time.monotonic() >= timeout:
                        raise RuntimeError('发送进程未到达屏障')
                    time.sleep(0.0005)
                previous = {r['packet_id'] for r in packets if r['offset_ns'] < stage['offset_us'] * 1000}
                while True:
                    received = {r['packet_id'] for r in load_events(directory / 'destination.jsonl') if r.get('kind') == 'frame' and r.get('src') == '10.0.0.1'}
                    if received >= previous:
                        drained = time.monotonic_ns()
                        break
                    if time.monotonic() >= timeout:
                        raise RuntimeError('配置更新前业务未排空')
                    time.sleep(0.0005)
                while (left := origin + stage['offset_us'] * 1000 - time.monotonic_ns()) > 0:
                    time.sleep(min(left / 1e9, 0.001))
                begin = len(rpc_log)
                for w in stage['writes']:
                    clients[w.node - 1].write(w.register, w.flow, w.value, kind=stage['kind'])
                finished = time.monotonic_ns()
                next_offset = min(r['offset_ns'] for r in packets if r['offset_ns'] >= stage['offset_us'] * 1000)
                if finished >= origin + next_offset:
                    raise RuntimeError('控制操作超过下一个预声明业务包时间')
                case['updates'].append({'offset_us': stage['offset_us'], 'kind': stage['kind'],
                                        'writes': [asdict(w) for w in stage['writes']], 'drained_monotonic_ns': drained,
                                        'after_monotonic_ns': finished, 'rpc_indices': [begin, len(rpc_log)]})
                (directory / f'barrier-{index}.resume').write_text('resume\n')
            wait_sender(sending)
            sending = None
            while (left := origin + 3200000000 - time.monotonic_ns()) > 0:
                time.sleep(min(left / 1e9, 0.01))
            time.sleep(0.1)
            for handle in handles:
                finish_capture(handle)
            handles = []
            case.update(verify(directory, packets, candidate, stages, clients, not a.ordinary))
            dump(directory / 'result.json', case)
            manifest['cases'].append(case)
            dump(out / 'manifest.json', manifest)
            if case['status'] != 'passed':
                raise RuntimeError('独立核对失败 ' + str(case['errors'][:5]))
            print(name, case['record_counts'], 'execution passed', flush=True)
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
        for c in clients:
            c.close()
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
