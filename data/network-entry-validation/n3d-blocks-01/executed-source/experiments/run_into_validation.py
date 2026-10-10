"""版本 6 的连续业务与合法观察正确性试点，不比较方法性能。"""
import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import time
import traceback

from mininet.log import setLogLevel
from scapy.all import Ether, IP, Raw, UDP
from build_into import ROOT, build
sys.path.insert(0, str(ROOT / 'src'))
from collector.codec import decode_frame as decode_plain
from collector.tagged_codec import WireRecord, encode
from collector.into_state import IntoState
from control.into import ConfirmedFields, IntoClient
from fork_topology import create_fork
from orchestration.adaptive import AdaptiveProxy, AdaptiveSettings, history_entry, select
from orchestration.event_schema import FlowId, ObjectId, RpcCost
from orchestration.event_observation import CountObservation
from orchestration.rolling import Budget, Management, Settings, changes
from run_validation import finish_capture, load_events, software_version

FLOWS = (FlowId(20, 1001), FlowId(20, 2002))
OBJECTS = tuple(ObjectId(n) for n in (1, 2, 3))
ROUTES = {FLOWS[0]: (OBJECTS[0], OBJECTS[2]), FLOWS[1]: (OBJECTS[1], OBJECTS[2])}
SLOTS = dict(zip(FLOWS, (1, 2)))
CASES = ('identity', 'continuous', 'partial_failure', 'counter_wrap', 'causal_loop')
TRACES = ('trace_arrival', 'trace_gate_time', 'trace_generated', 'trace_value', 'trace_decision',
          'trace_count_before', 'trace_threshold', 'trace_previous', 'trace_valid', 'trace_capacity',
          'trace_flow_index', 'trace_registered', 'trace_telemetry', 'trace_ip_length', 'trace_mtu')
SOURCES = ('experiments/run_into_validation.py', 'experiments/build_into.py', 'experiments/into_io.py',
           'experiments/into_notice.py', 'experiments/fork_io.py', 'experiments/fork_topology.py',
           'experiments/topology.py', 'experiments/strong_rpc.py', 'experiments/packet_io.py',
           'experiments/run_validation.py', 'experiments/build.py', 'experiments/check_into_validation.py',
           'experiments/check_adaptive_reference.py', 'experiments/check_rolling_reference.py',
           'src/p4/into_int.p4', 'src/control/into.py', 'src/control/metered.py', 'src/control/bmv2.py',
           'src/collector/into_state.py', 'src/collector/tagged_frame.py', 'src/collector/tagged_codec.py',
           'src/collector/codec.py', 'src/orchestration/adaptive.py', 'src/orchestration/rolling.py',
           'src/orchestration/event_schema.py', 'src/orchestration/event_engine.py',
           'src/orchestration/event_metrics.py', 'src/orchestration/event_observation.py', 'tests/test_into_live.py')


def plain(value):
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def dump(path, value):
    path.write_text(json.dumps(plain(value), ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def jsonlines(path, rows):
    path.write_text(''.join(json.dumps(plain(r), ensure_ascii=False, allow_nan=False) + '\n' for r in rows))


def available_rows(path):
    raw = path.read_bytes()
    return [json.loads(line) for line in raw[:raw.rfind(b'\n') + 1].splitlines() if line]


def packet(pid, flow, offset_us, length=120, stale=False):
    payload = pid.to_bytes(4, 'big') + bytes([flow]) * (length - 32)
    original = bytes(Ether(src='02:00:00:00:00:01', dst='02:00:00:00:00:02') /
                     IP(src='10.0.0.1', dst='10.0.0.2', ttl=64, id=pid) /
                     UDP(sport=10100 + flow, dport=9000) / Raw(payload))
    wire = original
    if stale:
        tagged = encode(FlowId(19, 1001), (WireRecord(1, 1, 123, 0),), 64)
        wire = bytes(Ether(src='02:00:00:00:00:01', dst='02:00:00:00:00:02', type=0x88b6) /
                     IP(src='10.0.0.1', dst='10.0.0.2', ttl=64, id=pid, proto=17) /
                     Raw(tagged + original[34:]))
    return {'packet_id': pid, 'flow': flow, 'path': [flow, 3] if flow < 3 else [3],
            'offset_ns': offset_us * 1000, 'ip_length': length, 'frame_hex': wire.hex(),
            'expected_transport_hex': decode_plain(original)['transport_hex'], 'payload_hex': payload.hex(),
            'registered': flow < 3 and not stale, 'injected_stale_session': stale}


def inputs(name, base):
    rate, duration = (8, 10000000) if name == 'causal_loop' else (40, 4000000) if name in ('continuous', 'partial_failure') else (20, 2000000)
    result = []
    for flow in (1, 2):
        end = 1000000 if name == 'counter_wrap' and flow == 1 else duration
        for t in range(10000 + (flow - 1) * 5000, end, 1000000 // rate):
            result.append(packet(0, flow, t))
    if name == 'identity':
        result += [packet(0, 3, 500000), packet(0, 1, 700000, 1494),
                   packet(0, 1, 800000, 1480), packet(0, 1, 900000, 1476),
                   packet(0, 1, 1100000, stale=True)]
    result.sort(key=lambda r: (r['offset_ns'], r['flow']))
    for i, row in enumerate(result):
        result[i] = packet(base + i, row['flow'], row['offset_ns'] // 1000, row['ip_length'], row['injected_stale_session'])
    return result, duration


def spawn_capture(host, directory, name, serve=False):
    ready, stop = directory / (name + '.ready'), directory / (name + '.stop')
    log = (directory / (name + '.log')).open('w')
    args = [sys.executable, str(ROOT / 'experiments/into_io.py'), 'capture', '--iface', host.defaultIntf().name,
            '--pcap', str(directory / (name + '.pcap')), '--events', str(directory / (name + '.jsonl')),
            '--ready', str(ready), '--stop', str(stop)]
    if serve:
        args.append('--serve')
    process = host.popen(args, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 5
    while not ready.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            process.terminate()
            process.wait(timeout=3)
            log.close()
            raise RuntimeError('抓包启动失败 ' + name)
        time.sleep(.01)
    return process, stop, log


def configure(clients, switches):
    for node, client in clients.items():
        client.default('TTIngress.node_config', 'TTIngress.set_node', [(node, 8), (int(node < 3), 1), (int(node == 4), 1), (1500, 16)])
        client.default('TTIngress.ipv4_forward', 'TTIngress.forward', [(2, 9), (0x020000000002, 48), (0x020000100000 + node, 48)])
        for flow, slot in SLOTS.items():
            client.register_flow(10100 + slot, slot, flow.tag, flow.session)
    switches[3].cli(['mirroring_add 100 3'])


def cold(clients, diagnostic, wrap=False):
    for node, client in clients.items():
        if node == 4:
            continue
        for name in ('last_timestamp', 'has_sample'):
            client.reset('TTEgress.' + name)
        client.reset('TTIngress.packet_count')
        if diagnostic:
            for name in TRACES:
                client.reset('TTEgress.' + name)
            client.reset('TTIngress.trace_source_count')
        if wrap and node < 3:
            client.write('TTIngress.packet_count', node, 2**32 - 5)


def state_keys(keys):
    return [{'node': n, 'flow': f, 'field': field} for n, f, field in keys]


def wait_until(target_ns):
    while (left := target_ns - time.monotonic_ns()) > 0:
        time.sleep(min(left / 1e9, .002))


def do_case(name, directory, source, destination, collector, clients, switches, diagnostic, packets, duration, rpc_log):
    from check_into_validation import check_case
    dump(directory / 'packets.json', packets)
    cold(clients, diagnostic, name == 'counter_wrap')
    handles, sender, producer, notice = [], None, None, None
    try:
        handles = [spawn_capture(destination, directory, 'destination', True), spawn_capture(collector, directory, 'collector')]
        origin = time.monotonic_ns() + 700000000
        backend, state = ConfirmedFields(clients, SLOTS, origin), IntoState(ROUTES, origin)
        initial = (50000, None, None, None) if name == 'causal_loop' else (1, 1, 1, 1) if name == 'identity' else (200000, 50000, 200000, 50000)
        keys = tuple((o.node, f) for f in sorted(ROUTES) for o in ROUTES[f])
        for (node, flow), value in zip(keys, initial):
            backend.write(node, flow, 'threshold_us', value or 50000, kind='installation')
            backend.write(node, flow, 'enabled', value is not None, kind='installation')
        case = {'name': name, 'status': 'running', 'origin_monotonic_ns': origin, 'duration_us': duration,
                'initial_config': initial, 'warmup_us': [0, 2000000] if name == 'causal_loop' else [0, 0],
                'measurement_window_us': [2000000, 10000000] if name == 'causal_loop' else [0, duration],
                'initial_source_counts': {str(i): 2**32 - 5 if name == 'counter_wrap' else 0 for i in (1, 2)},
                'scope': '确定性正确性案例，无强制发包空隙，无性能排名', 'diagnostics': diagnostic}
        dump(directory / 'result.json', case)
        sender = source.popen([sys.executable, str(ROOT / 'experiments/fork_io.py'), '--ifaces',
                              *[source.intfs[i].name for i in range(3)], '--packets', str(directory / 'packets.json'),
                              '--events', str(directory / 'send.jsonl'), '--start-ns', str(origin)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        notices = []
        if name == 'causal_loop':
            notices = [{'offset_us': t, 'message': {'version': v, 'demands': [
                {'node': n, 'target_us': 150000, 'weight': 1, 'active': n == active} for n in (1, 2, 3)]}}
                       for t, v, active in ((0, 1, 1), (3000000, 2, 3))]
            dump(directory / 'notice-schedule.json', notices)
            path = str(directory / 'notice.sock')
            if len(path.encode()) >= 108:
                raise ValueError('管理接口路径过长')
            notice = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            notice.bind(path)
            notice.setblocking(False)
            producer = subprocess.Popen([sys.executable, str(ROOT / 'experiments/into_notice.py'),
                '--socket', path, '--schedule', str(directory / 'notice-schedule.json'), '--events', str(directory / 'notice-send.jsonl'),
                '--start-ns', str(origin)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        settings = Settings()
        adaptive = AdaptiveSettings(gain_margin=.05)
        management = Management(ROUTES, {o: f'e{o.node}' for o in OBJECTS}, RpcCost(94, 30, '网络写入待核对'),
            {'enabled': RpcCost(89, 30, '实际容量寄存器写入'), 'threshold_us': RpcCost(94, 30, '实际阈值写入')},
            RpcCost(81, 40, '实际源计数读取'), RpcCost(1, 0, 'UNIX 数据报实际长度另列，未按模型 128 B 计'))
        budget = Budget({'e1': 3000, 'e2': 3000, 'e3': 4000}, 6000, 1200)
        dump(directory / 'algorithm-settings.json', {'settings': settings, 'adaptive': adaptive, 'budget': budget,
             'scope': '宽松正确性预算与固定初态，不含本轮静态训练或公平性能比较'})
        decisions, history, reads, received_notices = [], [], [], []
        interval = 1000000 if name == 'causal_loop' else 100000
        for offset in range(0, duration, interval):
            wait_until(origin + offset * 1000)
            backend.poll(time.monotonic_ns())
            if name == 'continuous':
                for t, node, flow, field, value in ((1100000, 1, FLOWS[0], 'enabled', False),
                         (2100000, 1, FLOWS[0], 'threshold_us', 1000000),
                         (2100000, 1, FLOWS[0], 'enabled', True), (3100000, 1, FLOWS[0], 'threshold_us', 50000)):
                    if t == offset:
                        backend.write(node, flow, field, value)
            if name == 'partial_failure':
                if offset == 700000:
                    backend.write(1, FLOWS[0], 'enabled', False)
                if offset == 1100000:
                    backend.write(1, FLOWS[0], 'threshold_us', 1000000, delay_ns=300000000)
                    backend.write(1, FLOWS[0], 'enabled', True, bad_register=True)
                if offset == 1600000:
                    reconciliation = backend.reconcile()
                    dump(directory / 'reconciliation.json', reconciliation)
                if offset == 2000000:
                    try:
                        clients[1].read('TTIngress.missing_for_fault', 1, kind='controlled_failed_read')
                    except Exception:
                        pass
                if offset == 2200000:
                    backend.write(1, FLOWS[0], 'enabled', True)
                if offset == 3100000:
                    backend.write(1, FLOWS[0], 'threshold_us', 50000)
            if notice is not None:
                while True:
                    try:
                        payload = notice.recv(65535)
                    except BlockingIOError:
                        break
                    available = time.monotonic_ns()
                    message = json.loads(payload)
                    state.notify(message, available)
                    received_notices.append({'received_ns': available, 'payload_bytes': len(payload), 'payload_hex': payload.hex(), 'message': message})
            cutoff = time.monotonic_ns()
            state.ingest(available_rows(directory / 'collector.jsonl'), cutoff)
            view = state.snapshot(cutoff, backend.confirmations)
            unresolved = state_keys(backend.unresolved())
            row = {'scheduled_offset_us': offset, 'cutoff_ns': cutoff, 'view': view, 'unresolved': unresolved, 'writes': []}
            if name == 'causal_loop' and not unresolved and state.notifications:
                proxy = AdaptiveProxy(view, management, budget, settings, adaptive, tuple(history))
                selected = select(proxy, initial)
                planned = changes(selected['config'], proxy.fields, management)
                planned.sort(key=lambda w: 0 if w[1] == 'enabled' and not w[2] else 1)
                for (node, flow), field, value in planned:
                    row['writes'].append(backend.write(node, flow, field, value))
                row.update(selected=selected, current_config=proxy.current, rates=[{'flow': f, 'rate_pps': proxy.rates.get(f)} for f in sorted(ROUTES)],
                           profiles=proxy.profiles, budget_diagnostic=proxy.budget_diagnostic, budget_history=tuple(history),
                           candidate_scores=list(proxy.memo.values()))
                entry = history_entry(proxy, selected)
                if entry is not None:
                    history.append(entry)
            else:
                row['reason'] = 'configuration_unresolved' if unresolved else 'awaiting_notice' if name == 'causal_loop' else 'forced_correctness_schedule'
            decisions.append(row)
            if offset % (1000000 if name == 'causal_loop' else 500000) == 0:
                for flow, slot in SLOTS.items():
                    client = clients[slot]
                    index = len(rpc_log)
                    value = client.read('TTIngress.packet_count', slot)
                    rpc = rpc_log[index]
                    observation = CountObservation(f'{name}-read-{index}', flow,
                        (rpc['before_monotonic_ns'] - origin) // 1000, (rpc['after_monotonic_ns'] - origin) // 1000, value)
                    state.counts.append(observation)
                    reads.append({'rpc_index': index, 'observation': observation})
            jsonlines(directory / 'decisions.jsonl', decisions)
        stdout, stderr = sender.communicate(timeout=5)
        if sender.returncode:
            raise RuntimeError('发包失败 ' + stderr.decode()[-500:])
        sender = None
        wait_until(origin + (duration + 250000) * 1000)
        if producer:
            stdout, stderr = producer.communicate(timeout=5)
            if producer.returncode:
                raise RuntimeError('通知发送失败 ' + stderr.decode()[-500:])
            producer = None
        for handle in handles:
            finish_capture(handle)
        handles = []
        final_ns = time.monotonic_ns()
        backend.poll(final_ns)
        state.ingest(load_events(directory / 'collector.jsonl'), final_ns)
        jsonlines(directory / 'ingestion.jsonl', state.ingestion)
        jsonlines(directory / 'actions.jsonl', backend.actions)
        jsonlines(directory / 'count-observations.jsonl', reads)
        jsonlines(directory / 'notices.jsonl', received_notices)
        observations, final = [], []
        if diagnostic:
            traces = {n: {t: c.read_all('TTEgress.' + t) for t in TRACES} for n, c in clients.items() if n < 4}
            source_traces = {n: clients[n].read_all('TTIngress.trace_source_count') for n in (1, 2)}
            for p in packets:
                for node in p['path']:
                    observations.append({'packet_id': p['packet_id'], 'node': node,
                        **{t: traces[node][t][p['packet_id']] for t in TRACES},
                        'source_count': source_traces[node][p['packet_id']] if node in (1, 2) else None})
            jsonlines(directory / 'observations.jsonl', observations)
        for n, c in clients.items():
            if n < 4:
                for flow, slot in SLOTS.items():
                    final.append({'node': n, 'flow': flow, 'slot': slot,
                        'last_timestamp': c.read('TTEgress.last_timestamp', slot, kind='diagnostic_read'),
                        'has_sample': c.read('TTEgress.has_sample', slot, kind='diagnostic_read'),
                        'threshold_us': c.read('TTEgress.time_threshold', slot, kind='diagnostic_read'),
                        'capacity': c.read('TTEgress.max_count', slot, kind='diagnostic_read'),
                        'source_count': c.read('TTIngress.packet_count', slot, kind='diagnostic_read')})
        dump(directory / 'final-state.json', final)
        case['ended_monotonic_ns'] = time.monotonic_ns()
        dump(directory / 'result.json', case)
        case.update(check_case(directory, rpc_log))
        dump(directory / 'result.json', case)
        return case
    except Exception as exc:
        if 'case' in locals():
            case.update(status='failed', error=str(exc), traceback=traceback.format_exc())
            dump(directory / 'result.json', case)
        raise
    finally:
        for process in (sender, producer):
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
        for handle in handles:
            finish_capture(handle)
        if notice:
            notice.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ordinary', action='store_true')
    parser.add_argument('--cases', choices=CASES, nargs='+', default=list(CASES))
    parser.add_argument('--steady-build', type=Path, default=ROOT / 'build/bmv2-steady-01')
    args = parser.parse_args()
    if os.geteuid():
        parser.error('需要 sudo 运行')
    setLogLevel('warning')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    program = build(not args.ordinary)
    binary = args.steady_build.resolve() / 'install/bin/simple_switch'
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if digest != json.loads((args.steady_build / 'manifest.json').read_text())['binary_sha256']:
        raise ValueError('隔离交换机摘要不一致')
    launch = out / 'launch'
    launch.mkdir()
    wrapper = launch / 'simple_switch'
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(binary)) + ' "$@"\n')
    wrapper.chmod(0o755)
    os.environ['PATH'] = str(launch) + ':' + os.environ['PATH']
    manifest = {'run_id': out.name, 'status': 'running', 'start_utc': datetime.now(timezone.utc).isoformat(),
                'scope': '连续业务正确性试点，强制场景不作性能排名', 'diagnostics': not args.ordinary,
                'seed': 0, 'format': 'version 6, header 12 B, record 12 B, K=1', 'cases': [],
                'topology': {'routes': [{'flow': f, 'objects': r} for f, r in ROUTES.items()], 'sink': 4, 'flow_slots': [1, 2]},
                'software': {'python': sys.version, 'p4c': software_version(['p4c-bm2-ss', '--version']),
                    'bmv2': software_version([str(binary), '--version']), 'mininet': software_version(['mn', '--version'])},
                'host': {'cpu_count': os.cpu_count(), 'load_start': list(os.getloadavg())},
                'source_sha256': {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in SOURCES},
                'steady_binary_sha256': digest, 'p4_json_sha256': hashlib.sha256(program.read_bytes()).hexdigest(),
                'notice_interface': '真实本地 UNIX 数据报有效负载计费，无管理网络帧或 128 B 假设',
                'observation': '实际收报、计数与确认；发送日志、IP ID 及诊断仅供离线核对',
                'control': '序列化 API 与实际 loopback 帧分列，镜像安装 API 仅有 CLI 与全量帧',
                'declared_correctness_limits': {'case_inband_per_link_bytes': 20000, 'case_export_padded_bytes': 20000,
                    'whole_run_control_frame_bytes': 134217728}}
    for name in SOURCES:
        target = out / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (out / 'compiled-program.json').write_bytes(program.read_bytes())
    frozen = {name: inputs(name, CASES.index(name) * 500) for name in args.cases}
    dump(out / 'frozen-inputs.json', frozen)
    regression = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                                cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (out / 'unit-tests.txt').write_text(regression.stdout)
    manifest['unit_regression'] = {'exit_code': regression.returncode, 'log': 'unit-tests.txt'}
    dump(out / 'manifest.json', manifest)
    if regression.returncode:
        raise RuntimeError('回归未通过')
    network, clients, rpc_handle = None, {}, None
    rpc_log = []
    started = time.monotonic()
    try:
        ready, stop = out / 'rpc.ready', out / 'rpc.stop'
        log = (out / 'rpc-capture.log').open('w')
        process = subprocess.Popen([sys.executable, str(ROOT / 'experiments/strong_rpc.py'),
            '--events', str(out / 'rpc.jsonl'), '--pcap', str(out / 'rpc.pcap'), '--ready', str(ready),
            '--stop', str(stop), '--ports', '9541', '9542', '9543', '9544'], stdout=log, stderr=subprocess.STDOUT)
        rpc_handle = process, stop, log
        deadline = time.monotonic() + 5
        while not ready.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('控制捕获未启动')
            time.sleep(.01)
        network, source, destination, collector, switches = create_fork(program, out, prefix='ti', base_port=9540)
        clients = {n: IntoClient(s.thrift_port, n, rpc_log) for n, s in enumerate(switches, 1)}
        configure(clients, switches)
        for name in args.cases:
            directory = out / name
            directory.mkdir()
            case = do_case(name, directory, source, destination, collector, clients, switches, not args.ordinary, *frozen[name], rpc_log)
            manifest['cases'].append(case)
            dump(out / 'manifest.json', manifest)
            print(json.dumps({'case': name, 'status': case['status'], 'packets': case['packet_count'],
                              'reports': case['report_count'], 'errors': case['errors'][:5]}), flush=True)
            if case['status'] != 'passed':
                raise RuntimeError('正确性检查失败 ' + name)
        manifest['status'] = 'passed'
    except Exception as exc:
        manifest.update(status='failed', error=str(exc), traceback=traceback.format_exc())
        print(traceback.format_exc(), file=sys.stderr)
    finally:
        for client in clients.values():
            client.close()
        if network:
            network.stop()
        if rpc_handle:
            finish_capture(rpc_handle)
        jsonlines(out / 'rpc-operations.jsonl', rpc_log)
        manifest.update(end_utc=datetime.now(timezone.utc).isoformat(), duration_seconds=time.monotonic() - started)
        manifest['host']['load_end'] = list(os.getloadavg())
        manifest['artifact_sha256'] = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out.rglob('*')) if p.is_file() and p.name != 'manifest.json' and 'executed-source' not in p.parts and 'launch' not in p.parts}
        dump(out / 'manifest.json', manifest)
    return int(manifest['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
