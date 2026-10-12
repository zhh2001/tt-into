"""文献训练、校准与十方法比较的共同实际入口。"""
import json
import socket
import subprocess
import sys
import time
import traceback

from run_into_validation import (ROOT, ROUTES, SLOTS, ConfirmedFields, IntoState, CountObservation,
    cold, dump, finish_capture, jsonlines, load_events, spawn_capture, state_keys, wait_until, plain)
from run_network_static import wall_sample, aligned_cutoff
from rolling_workloads import budget_for
from control.matched_live import LivePlanner, OwnControl
from control.literature_live import LiteraturePlanner
from orchestration.literature import LiteratureSettings
from collector.available_prefix import ingest_prefix
from collector.observer_schedule import ObserverSchedule


def planner_for(entry, management, budget, params):
    if entry['method'] in ('intopp_due', 'd_into_tt'):
        return LiteraturePlanner(entry['method'], management, budget, params, entry['initial_config'],
                                 LiteratureSettings(**entry['literature']))
    return LivePlanner(entry['method'], management, budget, params, entry['initial_config'])


def do_case(entry, directory, management, source, destination, collector, clients, rpc_log, inputs, params):
    from check_literature_matrix import check_case
    name, method = entry['name'], entry['method']
    packets, notices, tasks = inputs
    for filename, value in (('packets.json', packets), ('tasks.json', tasks), ('notice-schedule.json', notices)):
        dump(directory / filename, value)
    start_index = len(rpc_log)
    cold(clients, False)
    handles, sender, producer, channel = [], None, None, None
    observations, availability, decisions, reads, received, reconciliation, clocks = [], [], [], [], [], [], []
    try:
        handles = [spawn_capture(destination, directory, 'destination', True), spawn_capture(collector, directory, 'collector')]
        origin = ((time.monotonic_ns() + 800000000 + 999) // 1000) * 1000
        schedule = ObserverSchedule(origin)
        backend, state = ConfirmedFields(clients, SLOTS, origin), IntoState(ROUTES, origin)
        account = OwnControl(rpc_log, origin, start_index)
        budget = budget_for(entry['scenario'], entry['budget_level'])
        planner = planner_for(entry, management, budget, params)
        keys = tuple((o.node, f) for f in sorted(ROUTES) for o in ROUTES[f])
        for (node, flow), value in zip(keys, entry['initial_config']):
            backend.write(node, flow, 'threshold_us', value or 50000, kind='installation')
            backend.write(node, flow, 'enabled', value is not None, kind='installation')
        case = dict(entry, status='running', origin_monotonic_ns=origin, duration_us=10000000,
                    diagnostics=False, initial_source_counts={'1': 0, '2': 0}, rpc_start_index=start_index,
                    observation_period_us=100000, decision_period_us=1000000,
                    warmup_us=[0, 2000000], measurement_window_us=[2000000, 10000000],
                    scope=entry['scope'])
        dump(directory / 'result.json', case)
        dump(directory / 'algorithm-settings.json', {'settings': planner.options, 'adaptive': params, 'literature': getattr(planner, 'literature', None), 'budget': budget,
             'notice_budget_bytes_per_s': 128, 'network_parameters_frozen': True,
             'observation_period_us': 100000, 'decision_period_us': 1000000,
             'parameter_origin': '各阶段执行前固定共同初始化及方法全局参数'})
        sock_path = directory.parent / f"notice-{entry['position']}.sock"
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        channel.bind(str(sock_path)); channel.setblocking(False)
        sender = source.popen([sys.executable, str(ROOT / 'experiments/fork_io.py'), '--ifaces',
            *[source.intfs[i].name for i in range(3)], '--packets', str(directory / 'packets.json'),
            '--events', str(directory / 'send.jsonl'), '--start-ns', str(origin)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        producer = subprocess.Popen([sys.executable, str(ROOT / 'experiments/into_notice.py'),
            '--socket', str(sock_path), '--schedule', str(directory / 'notice-schedule.json'),
            '--events', str(directory / 'notice-send.jsonl'), '--start-ns', str(origin)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        latest_index = None
        clocks.append(wall_sample())
        for index in range(schedule.slots):
            wait_until(origin + index * schedule.observation_us * 1000)
            slot = schedule.slot(index, time.monotonic_ns())
            observed = dict(slot, skipped=slot['observation_skipped'])
            if not observed['skipped']:
                rpc_before = len(rpc_log)
                event = ingest_prefix(state, directory / 'collector.jsonl')
                event.update(observation_index=index, scheduled_offset_us=slot['scheduled_offset_us'])
                observed.update(availability=event, rpc_start_index=rpc_before, rpc_stop_index=len(rpc_log),
                                fresh_reports=plain(state.reports[event['reports_before']:event['reports_after']]),
                                cache_after=plain([state.cache[k] for k in sorted(state.cache)]))
                availability.append(event); latest_index = index
            else:
                observed['reason'] = 'full_observation_slot_late'
            observed['finished_ns'] = time.monotonic_ns()
            observations.append(observed)
            if slot['decision_due']:
                clocks.append(wall_sample())
                if slot['decision_skipped']:
                    decisions.append({'scheduled_offset_us': slot['scheduled_offset_us'],
                        'cycle_started_ns': slot['cycle_started_ns'], 'skipped': True,
                        'reason': 'full_epoch_late', 'cycle_finished_ns': time.monotonic_ns(), 'observation_index': latest_index})
                    continue
                backend.poll(time.monotonic_ns())
                if backend.unresolved():
                    reconciliation.extend(backend.reconcile()); backend.poll(time.monotonic_ns())
                while True:
                    try:
                        recv_started = time.monotonic_ns(); payload = channel.recv(65535)
                    except BlockingIOError:
                        break
                    at = time.monotonic_ns(); message = json.loads(payload)
                    state.notify(message, at)
                    operation_id = f'notice-{message["version"]}'
                    account.notice(operation_id, payload, at)
                    received.append({'operation_id': operation_id, 'receive_started_ns': recv_started, 'received_ns': at,
                                     'payload_bytes': len(payload), 'payload_hex': payload.hex(), 'message': message})
                cutoff = aligned_cutoff(origin)
                view, control = state.snapshot(cutoff, backend.confirmations), account.snapshot(cutoff)
                row = {'scheduled_offset_us': slot['scheduled_offset_us'], 'cycle_started_ns': slot['cycle_started_ns'],
                       'cutoff_ns': cutoff, 'time_us': view.now_us, 'view': view, 'control_snapshot': control,
                       'observation_index': latest_index, 'unresolved': state_keys(backend.unresolved()),
                       'writes': [], 'reads': [], 'skipped': False}
                if view.now_us >= 10000000:
                    row.update(skipped=True, reason='measurement_finished')
                elif not row['unresolved']:
                    before = time.monotonic_ns(); chosen, planned = planner.decide(view, control)
                    row.update(chosen, solver_started_ns=before, solver_finished_ns=time.monotonic_ns())
                    for (node, flow), field, value in planned:
                        row['writes'].append(backend.write(node, flow, field, value))
                        if not row['writes'][-1]['success']: break
                else:
                    row['reason'] = 'configuration_unresolved'
                if method != 'static' and not row['skipped']:
                    for flow, flow_slot in SLOTS.items():
                        i = len(rpc_log); value = clients[flow_slot].read('TTIngress.packet_count', flow_slot)
                        rpc = rpc_log[i]
                        observation = CountObservation(f'{name}-read-{i}', flow,
                            (rpc['before_monotonic_ns'] - origin) // 1000, (rpc['after_monotonic_ns'] - origin) // 1000, value)
                        state.counts.append(observation)
                        read = {'rpc_index': i, 'observation': observation}; reads.append(read); row['reads'].append(read)
                row['cycle_finished_ns'] = time.monotonic_ns()
                row['after_control_snapshot'] = account.snapshot(row['cycle_finished_ns'])
                decisions.append(row)
                jsonlines(directory / 'decisions.jsonl', decisions)
            jsonlines(directory / 'report-observations.jsonl', observations)
        for process in (sender, producer):
            _, stderr = process.communicate(timeout=5)
            if process.returncode: raise RuntimeError('业务或通知进程异常 ' + stderr.decode()[-500:])
        sender, producer = None, None
        wait_until(origin + 10250000000); clocks.append(wall_sample())
        for handle in handles: finish_capture(handle)
        handles = []
        final_ns = time.monotonic_ns(); backend.poll(final_ns)
        # 归档读取只用于拒绝/身份核对，不进入测量观察事件或在线年龄。
        state.ingest(load_events(directory / 'collector.jsonl'), final_ns)
        final = []
        for node, client in clients.items():
            if node < 4:
                for flow, flow_slot in SLOTS.items():
                    final.append({'node': node, 'flow': flow, 'slot': flow_slot,
                        'last_timestamp': client.read('TTEgress.last_timestamp', flow_slot, kind='diagnostic_read'),
                        'has_sample': client.read('TTEgress.has_sample', flow_slot, kind='diagnostic_read'),
                        'threshold_us': client.read('TTEgress.time_threshold', flow_slot, kind='diagnostic_read'),
                        'capacity': client.read('TTEgress.max_count', flow_slot, kind='diagnostic_read'),
                        'source_count': client.read('TTIngress.packet_count', flow_slot, kind='diagnostic_read')})
        dump(directory / 'final-state.json', final)
        jsonlines(directory / 'actions.jsonl', backend.actions)
        jsonlines(directory / 'count-observations.jsonl', reads); jsonlines(directory / 'notices.jsonl', received)
        jsonlines(directory / 'ingestion.jsonl', state.ingestion); jsonlines(directory / 'availability.jsonl', availability)
        jsonlines(directory / 'report-observations.jsonl', observations); jsonlines(directory / 'decisions.jsonl', decisions)
        dump(directory / 'reconciliation.json', reconciliation); dump(directory / 'wall-clock-samples.json', clocks)
        case.update(ended_monotonic_ns=time.monotonic_ns(), rpc_stop_index=len(rpc_log))
        dump(directory / 'control-ledger.json', account.snapshot(case['ended_monotonic_ns']))
        dump(directory / 'result.json', case)
        case.update(check_case(directory, rpc_log)); dump(directory / 'result.json', case)
        return case
    except Exception as exc:
        if 'case' in locals():
            for filename, values in (('actions.jsonl', backend.actions), ('decisions.jsonl', decisions),
                ('count-observations.jsonl', reads), ('notices.jsonl', received), ('availability.jsonl', availability),
                ('report-observations.jsonl', observations), ('ingestion.jsonl', state.ingestion)):
                jsonlines(directory / filename, values)
            case.update(status='failed', error=str(exc), traceback=traceback.format_exc()); dump(directory / 'result.json', case)
        raise
    finally:
        for process in (sender, producer):
            if process and process.poll() is None: process.terminate(); process.wait(timeout=3)
        for handle in handles: finish_capture(handle)
        if channel: channel.close(); sock_path.unlink(missing_ok=True)
