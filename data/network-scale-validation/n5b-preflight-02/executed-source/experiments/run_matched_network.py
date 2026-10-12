"""普通版共同网格真实闭环开发试点，所有方法保持冻结设置。"""
import argparse
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
from run_into_validation import (ROOT, SOURCES as OLD_SOURCES, FLOWS, OBJECTS, ROUTES, SLOTS,
    IntoClient, ConfirmedFields, IntoState, CountObservation, available_rows, build, cold,
    configure, create_fork, dump, finish_capture, jsonlines, load_events, packet,
    software_version, spawn_capture, state_keys, wait_until)
from run_matched_reference import (AdaptiveSettings, GUARDED, METHODS, PARAMETERS, matched_management,
                                    measured_fees, plain as model_plain, settings, sha, workload)
from rolling_workloads import budget_for
from control.matched_live import LivePlanner, OwnControl

PROFILES = (('stable', 'high'), ('shared_priority', 'high'), ('switch', 'low'), ('bursty_jitter', 'low'))
SOURCES = tuple(dict.fromkeys(OLD_SOURCES + ('src/control/matched_live.py', 'src/orchestration/control_ledger.py',
    'src/orchestration/matched_grid.py', 'src/orchestration/accounted_engine.py',
    'experiments/run_matched_network.py', 'experiments/check_matched_network.py',
    'experiments/run_matched_reference.py', 'experiments/run_matched_preflight.py',
    'experiments/run_rolling_reference.py', 'experiments/rolling_workloads.py', 'experiments/adaptive_workloads.py',
    'experiments/rolling_rpc_cost.py', 'tests/test_matched_live.py')))


def wall_sample():
    before = time.monotonic_ns()
    wall = time.time_ns()
    return {'before_monotonic_ns': before, 'wall_ns': wall, 'after_monotonic_ns': time.monotonic_ns()}


def aligned_cutoff(origin):
    cutoff = origin + ((time.monotonic_ns() - origin + 999) // 1000) * 1000
    wait_until(cutoff)
    return cutoff


def make_inputs(scenario, base):
    w = workload(scenario, 11)
    packets = [packet(base + i, FLOWS.index(p.flow) + 1, p.hops[0].ingress_us, p.ip_bytes) for i, p in enumerate(w.packets)]
    notices = [{'offset_us': n.released_us, 'message': json.loads(n.payload)} for n in w.notices]
    return packets, notices, model_plain(w.tasks)


def do_case(name, directory, case_index, scenario, level, method, initial, params, management,
            source, destination, collector, clients, rpc_log, packets, notices, tasks):
    from check_matched_network import check_case
    dump(directory / 'packets.json', packets)
    dump(directory / 'tasks.json', tasks)
    dump(directory / 'notice-schedule.json', notices)
    start_index = len(rpc_log)
    cold(clients, False)
    handles, sender, producer, channel = [], None, None, None
    try:
        handles = [spawn_capture(destination, directory, 'destination', True), spawn_capture(collector, directory, 'collector')]
        origin = ((time.monotonic_ns() + 800000000 + 999) // 1000) * 1000
        backend, state = ConfirmedFields(clients, SLOTS, origin), IntoState(ROUTES, origin)
        account = OwnControl(rpc_log, origin, start_index)
        budget = budget_for(scenario, level)
        planner = LivePlanner(method, management, budget, params, initial)
        keys = tuple((o.node, f) for f in sorted(ROUTES) for o in ROUTES[f])
        for (node, flow), value in zip(keys, initial):
            backend.write(node, flow, 'threshold_us', value or 50000, kind='installation')
            backend.write(node, flow, 'enabled', value is not None, kind='installation')
        case = {'name': name, 'status': 'running', 'scenario': scenario, 'budget_level': level, 'method': method,
                'seed': 11, 'origin_monotonic_ns': origin, 'duration_us': 10000000, 'diagnostics': False,
                'initial_config': initial, 'warmup_us': [0, 2000000], 'measurement_window_us': [2000000, 10000000],
                'initial_source_counts': {'1': 0, '2': 0}, 'rpc_start_index': start_index,
                'scope': '已消费训练业务的普通版开发试点，非盲测或统计性能排名'}
        dump(directory / 'result.json', case)
        dump(directory / 'algorithm-settings.json', {'settings': planner.options, 'adaptive': params, 'budget': budget,
             'notice_budget_bytes_per_s': 128, 'model_parameters_frozen': True})
        sock_path = directory.parent / f'notice-{case_index}.sock'
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        channel.bind(str(sock_path))
        channel.setblocking(False)
        sender = source.popen([sys.executable, str(ROOT / 'experiments/fork_io.py'), '--ifaces',
            *[source.intfs[i].name for i in range(3)], '--packets', str(directory / 'packets.json'),
            '--events', str(directory / 'send.jsonl'), '--start-ns', str(origin)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        producer = subprocess.Popen([sys.executable, str(ROOT / 'experiments/into_notice.py'),
            '--socket', str(sock_path), '--schedule', str(directory / 'notice-schedule.json'),
            '--events', str(directory / 'notice-send.jsonl'), '--start-ns', str(origin)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        decisions, reads, received, reconciliation, clocks = [], [], [], [], [wall_sample()]
        for epoch in range(10):
            scheduled = origin + epoch * 1000000000
            wait_until(scheduled)
            began = time.monotonic_ns()
            clocks.append(wall_sample())
            if began >= scheduled + 1000000000:
                decisions.append({'scheduled_offset_us': epoch * 1000000, 'cycle_started_ns': began,
                                  'skipped': True, 'reason': 'full_epoch_late', 'cycle_finished_ns': time.monotonic_ns()})
                continue
            backend.poll(began)
            if backend.unresolved():
                reconciliation.extend(backend.reconcile())
                backend.poll(time.monotonic_ns())
            while True:
                try:
                    payload = channel.recv(65535)
                except BlockingIOError:
                    break
                at = time.monotonic_ns()
                message = json.loads(payload)
                state.notify(message, at)
                operation_id = f'notice-{message["version"]}'
                account.notice(operation_id, payload, at)
                received.append({'operation_id': operation_id, 'received_ns': at, 'payload_bytes': len(payload),
                                 'payload_hex': payload.hex(), 'message': message})
            cutoff = aligned_cutoff(origin)
            state.ingest(available_rows(directory / 'collector.jsonl'), cutoff)
            view, control = state.snapshot(cutoff, backend.confirmations), account.snapshot(cutoff)
            row = {'scheduled_offset_us': epoch * 1000000, 'cycle_started_ns': began, 'cutoff_ns': cutoff,
                   'time_us': view.now_us, 'view': view, 'control_snapshot': control,
                   'unresolved': state_keys(backend.unresolved()), 'writes': [], 'reads': [], 'skipped': False}
            if view.now_us >= 10000000:
                row.update(skipped=True, reason='measurement_finished')
            elif not row['unresolved']:
                before = time.monotonic_ns()
                chosen, planned = planner.decide(view, control)
                row.update(chosen, solver_started_ns=before, solver_finished_ns=time.monotonic_ns())
                for (node, flow), field, value in planned:
                    row['writes'].append(backend.write(node, flow, field, value))
                    if not row['writes'][-1]['success']:
                        break
            else:
                row['reason'] = 'configuration_unresolved'
            if method != 'static' and not row['skipped']:
                for flow, slot in SLOTS.items():
                    i = len(rpc_log)
                    value = clients[slot].read('TTIngress.packet_count', slot)
                    rpc = rpc_log[i]
                    observation = CountObservation(f'{name}-read-{i}', flow,
                        (rpc['before_monotonic_ns'] - origin) // 1000, (rpc['after_monotonic_ns'] - origin) // 1000, value)
                    state.counts.append(observation)
                    read = {'rpc_index': i, 'observation': observation}
                    reads.append(read)
                    row['reads'].append(read)
            row['cycle_finished_ns'] = time.monotonic_ns()
            row['after_control_snapshot'] = account.snapshot(row['cycle_finished_ns'])
            decisions.append(row)
            jsonlines(directory / 'decisions.jsonl', decisions)
        for p in (sender, producer):
            _, stderr = p.communicate(timeout=5)
            if p.returncode:
                raise RuntimeError('业务或管理进程异常 ' + stderr.decode()[-500:])
        sender, producer = None, None
        wait_until(origin + 10250000000)
        clocks.append(wall_sample())
        for handle in handles:
            finish_capture(handle)
        handles = []
        final_ns = time.monotonic_ns()
        backend.poll(final_ns)
        state.ingest(load_events(directory / 'collector.jsonl'), final_ns)
        final = []
        for node, client in clients.items():
            if node < 4:
                for flow, slot in SLOTS.items():
                    final.append({'node': node, 'flow': flow, 'slot': slot,
                        'last_timestamp': client.read('TTEgress.last_timestamp', slot, kind='diagnostic_read'),
                        'has_sample': client.read('TTEgress.has_sample', slot, kind='diagnostic_read'),
                        'threshold_us': client.read('TTEgress.time_threshold', slot, kind='diagnostic_read'),
                        'capacity': client.read('TTEgress.max_count', slot, kind='diagnostic_read'),
                        'source_count': client.read('TTIngress.packet_count', slot, kind='diagnostic_read')})
        dump(directory / 'final-state.json', final)
        jsonlines(directory / 'actions.jsonl', backend.actions)
        jsonlines(directory / 'count-observations.jsonl', reads)
        jsonlines(directory / 'notices.jsonl', received)
        jsonlines(directory / 'ingestion.jsonl', state.ingestion)
        dump(directory / 'reconciliation.json', reconciliation)
        dump(directory / 'wall-clock-samples.json', clocks)
        case.update(ended_monotonic_ns=time.monotonic_ns(), rpc_stop_index=len(rpc_log))
        dump(directory / 'control-ledger.json', account.snapshot(case['ended_monotonic_ns']))
        dump(directory / 'result.json', case)
        case.update(check_case(directory, rpc_log))
        dump(directory / 'result.json', case)
        return case
    except Exception as exc:
        if 'case' in locals():
            # 异常原始记录保留，便于核查已完成部分及费用。
            jsonlines(directory / 'actions.jsonl', backend.actions)
            if 'decisions' in locals():
                jsonlines(directory / 'decisions.jsonl', decisions)
                jsonlines(directory / 'count-observations.jsonl', reads)
                jsonlines(directory / 'notices.jsonl', received)
            case.update(status='failed', error=str(exc), traceback=traceback.format_exc())
            dump(directory / 'result.json', case)
        raise
    finally:
        for p in (sender, producer):
            if p and p.poll() is None:
                p.terminate()
                p.wait(timeout=3)
        for handle in handles:
            finish_capture(handle)
        if channel:
            channel.close()
            sock_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--methods', nargs='+', choices=METHODS, default=list(METHODS))
    parser.add_argument('--profiles', nargs='+', choices=[s + '-' + b for s, b in PROFILES], default=[s + '-' + b for s, b in PROFILES])
    a = parser.parse_args()
    if os.geteuid():
        parser.error('需要 sudo 运行')
    if len(set(a.methods)) != len(a.methods) or len(set(a.profiles)) != len(a.profiles):
        parser.error('方法及开发配置不得重复')
    setLogLevel('warning')
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    program = build(False)
    binary = ROOT / 'build/bmv2-steady-01/install/bin/simple_switch'
    digest = sha(binary)
    if digest != json.loads((binary.parents[2] / 'manifest.json').read_text())['binary_sha256']:
        # manifest 位于隔离构建根而非 install。
        raise ValueError('隔离 steady_clock 摘要不同')
    launch = out / 'launch'
    launch.mkdir()
    wrapper = launch / 'simple_switch'
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(binary)) + ' "$@"\n')
    wrapper.chmod(0o755)
    os.environ['PATH'] = str(launch) + ':' + os.environ['PATH']
    model = ROOT / 'data/matched-reference/n3b-matched-01'
    selections = json.loads((model / 'training-selections.json').read_text())
    frozen = json.loads((model / 'frozen-parameters.json').read_text())
    reference = measured_fees(ROOT / 'data/into-validation/n3-validation-01')
    management = matched_management(reference)
    inputs = {}
    for i, (s, b, method) in enumerate((s, b, method) for s, b in PROFILES if s + '-' + b in a.profiles for method in a.methods):
        name = f'{s}-{b}-{method}'
        inputs[name] = {'scenario': s, 'budget_level': b, 'method': method, 'inputs': make_inputs(s, i * 200)}
    manifest = {'run_id': out.name, 'status': 'running', 'start_utc': datetime.now(timezone.utc).isoformat(), 'seed': 11,
        'scope': '八方法四开发配置的真实普通版试点，已消费业务，非盲测性能排名', 'methods': a.methods,
        'profiles': a.profiles, 'expected_cases': len(inputs), 'cases': [], 'settings': settings(),
        'topology': {'routes': [{'flow': f, 'objects': r} for f, r in ROUTES.items()]},
        'software': {'python': sys.version, 'p4c': software_version(['p4c-bm2-ss', '--version']),
            'bmv2': software_version([str(binary), '--version']), 'mininet': software_version(['mn', '--version'])},
        'host': {'cpu_count': os.cpu_count(), 'load_start': list(os.getloadavg())},
        'steady_binary_sha256': digest, 'p4_json_sha256': sha(program),
        'source_sha256': {n: sha(ROOT / n) for n in SOURCES},
        'model_reference': {n: sha(model / n) for n in ('manifest.json', 'training-selections.json', 'frozen-parameters.json')},
        'rpc_reference': reference, 'parameter_selection': frozen['selected_indices'],
        'timing': '实际单调截止、CPU 求解、同步 RPC，迟到整周期跳过；无模型时延替代',
        'observation': '实收身份/计数/确认/自有完成费用；发送与抓包只离线验证'}
    for n in SOURCES:
        p = out / 'executed-source' / n
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / n, p)
    shutil.copyfile(program, out / 'compiled-program.json')
    dump(out / 'frozen-inputs.json', inputs)
    dump(out / 'manifest.json', manifest)
    regression = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=ROOT,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (out / 'unit-tests.txt').write_text(regression.stdout)
    if regression.returncode:
        manifest.update(status='failed', error='单元回归失败')
        dump(out / 'manifest.json', manifest)
        return 1
    network, clients, rpc_handle, rpc_log = None, {}, None, []
    try:
        ready, stop = out / 'rpc.ready', out / 'rpc.stop'
        log = (out / 'rpc-capture.log').open('w')
        process = subprocess.Popen([sys.executable, str(ROOT / 'experiments/strong_rpc.py'), '--events', str(out / 'rpc.jsonl'),
            '--pcap', str(out / 'rpc.pcap'), '--ready', str(ready), '--stop', str(stop), '--ports',
            '9561', '9562', '9563', '9564'], stdout=log, stderr=subprocess.STDOUT)
        rpc_handle = process, stop, log
        deadline = time.monotonic() + 5
        while not ready.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('控制捕获未就绪')
            time.sleep(.01)
        network, source, destination, collector, switches = create_fork(program, out, prefix='tm', base_port=9560)
        clients = {n: IntoClient(s.thrift_port, n, rpc_log) for n, s in enumerate(switches, 1)}
        configure(clients, switches)
        for i, (name, config) in enumerate(inputs.items()):
            directory = out / name
            directory.mkdir()
            s, b, method = config['scenario'], config['budget_level'], config['method']
            initial = next(r['config'] for r in selections if (r['scenario'], r['budget_level']) == (s, b))
            params = PARAMETERS[frozen['selected_indices'][method]] if method in GUARDED else AdaptiveSettings()
            result = do_case(name, directory, i, s, b, method, initial, params, management, source, destination,
                             collector, clients, rpc_log, *config['inputs'])
            manifest['cases'].append(result)
            dump(out / 'manifest.json', manifest)
            print(json.dumps({'case': name, 'status': result['status'], 'packets': result['packet_count'],
                              'updates': result['successful_updates'], 'errors': result['errors'][:3]}), flush=True)
            if result['status'] != 'passed':
                raise RuntimeError('案例核对失败：' + name)
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
        manifest.update(end_utc=datetime.now(timezone.utc).isoformat())
        manifest['host']['load_end'] = list(os.getloadavg())
        manifest['artifact_sha256'] = {str(p.relative_to(out)): sha(p) for p in out.rglob('*') if p.is_file()
            and p.name != 'manifest.json' and 'executed-source' not in p.parts and 'launch' not in p.parts}
        dump(out / 'manifest.json', manifest)
    return int(manifest['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
