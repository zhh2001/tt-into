"""真实BMv2的共同100ms报告观察正确性试点，仅用已消费训练业务。"""
import argparse
from datetime import datetime, timezone
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
from run_into_validation import ROOT, ROUTES, IntoClient, build, configure, create_fork, dump, finish_capture, jsonlines, software_version
from run_matched_reference import AdaptiveSettings, matched_management, measured_fees, settings
from run_network_static import make_inputs, SOURCES as STATIC_SOURCES
from report_observer_contract import validate, sha
from report_observer_case import do_case
from network_host import snapshot as host_snapshot, interval as host_interval

SOURCES = tuple(dict.fromkeys(STATIC_SOURCES + ('src/collector/observer_schedule.py',
    'experiments/report_observer_contract.py', 'experiments/prepare_report_observer.py',
    'experiments/report_observer_case.py', 'experiments/run_report_observer.py',
    'experiments/check_report_observer.py', 'tests/test_report_observer.py')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--validate-only', action='store_true')
    a = parser.parse_args()
    if os.geteuid() and not a.validate_only: parser.error('需要sudo运行')
    plan = json.loads(a.plan.read_text()); frozen = json.loads((a.plan.parent / 'manifest.json').read_text())
    if sha(a.plan) != frozen['artifact_sha256']['observer-plan.json']: raise ValueError('观察清单摘要改变')
    validate(plan, current=True)
    if a.validate_only:
        print(json.dumps({'status': 'validated', 'cases': 8, 'inputs_generated': False, 'network_started': False})); return 0
    out = a.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    setLogLevel('warning'); program = build(False)
    binary = ROOT / 'build/bmv2-steady-01/install/bin/simple_switch'
    if sha(binary) != json.loads((binary.parents[2] / 'manifest.json').read_text())['binary_sha256']: raise ValueError('单调时钟交换机摘要不符')
    launch = out / 'launch'; launch.mkdir(); wrapper = launch / 'simple_switch'
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(binary)) + ' "$@"\n'); wrapper.chmod(0o755)
    os.environ['PATH'] = str(launch) + ':' + os.environ['PATH']
    inputs = {e['name']: dict(e, inputs=make_inputs(e['scenario'], 11, i * 200)) for i, e in enumerate(plan['execution_order'])}
    fees = measured_fees(ROOT / 'data/into-validation/n3-validation-01'); management = matched_management(fees)
    manifest = {'run_id': out.name, 'status': 'running', 'start_utc': datetime.now(timezone.utc).isoformat(),
        'scope': '8例已消费训练业务的实际共同观察正确性，无统计性能排名', 'stage': 'report_observer_correctness',
        'seed': 11, 'independent_test': False, 'observation_period_us': 100000, 'decision_period_us': 1000000,
        'expected_cases': 8, 'cases': [], 'execution_order': plan['execution_order'], 'settings': settings(),
        'topology': {'routes': [{'flow': f, 'objects': r} for f, r in ROUTES.items()]},
        'plan_sha256': sha(a.plan), 'method_contract_sha256': plan['method_contract_sha256'],
        'source_sha256': {n: sha(ROOT / n) for n in SOURCES}, 'steady_binary_sha256': sha(binary), 'p4_json_sha256': sha(program),
        'software': {'python': sys.version, 'p4c': software_version(['p4c-bm2-ss', '--version']),
                     'bmv2': software_version([str(binary), '--version']), 'mininet': software_version(['mn', '--version'])},
        'host': {'cpu_count': os.cpu_count(), 'load_start': list(os.getloadavg())}, 'rpc_reference': fees,
        'concurrent_activity': '网络期间不并发同宿主回归、离线分析或第二网络，实际端点负载逐例保存'}
    for n in SOURCES:
        target = out / 'executed-source' / n; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT / n, target)
    shutil.copyfile(program, out / 'compiled-program.json'); shutil.copyfile(a.plan, out / 'observer-plan.json')
    shutil.copyfile(a.plan.parent / 'method-contract.json', out / 'method-contract.json')
    dump(out / 'frozen-inputs.json', inputs); dump(out / 'execution-order.json', plan['execution_order']); dump(out / 'manifest.json', manifest)
    regression = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=ROOT,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (out / 'unit-tests.txt').write_text(regression.stdout)
    if regression.returncode:
        manifest.update(status='failed', error='单元回归失败'); dump(out / 'manifest.json', manifest); return 1
    network, clients, rpc_handle, rpc_log = None, {}, None, []
    try:
        ready, stop = out / 'rpc.ready', out / 'rpc.stop'; log = (out / 'rpc-capture.log').open('w')
        process = subprocess.Popen([sys.executable, str(ROOT / 'experiments/strong_rpc.py'), '--events', str(out / 'rpc.jsonl'),
            '--pcap', str(out / 'rpc.pcap'), '--ready', str(ready), '--stop', str(stop), '--ports',
            '9561', '9562', '9563', '9564'], stdout=log, stderr=subprocess.STDOUT)
        rpc_handle = process, stop, log; deadline = time.monotonic() + 5
        while not ready.exists():
            if process.poll() is not None or time.monotonic() > deadline: raise RuntimeError('控制捕获未就绪')
            time.sleep(.01)
        network, source, destination, collector, switches = create_fork(program, out, prefix='ro', base_port=9560)
        clients = {n: IntoClient(s.thrift_port, n, rpc_log) for n, s in enumerate(switches, 1)}; configure(clients, switches)
        for name, entry in inputs.items():
            directory = out / name; directory.mkdir(); before_host = host_snapshot(switches)
            result = do_case(entry, directory, management, source, destination, collector, clients, rpc_log,
                             entry['inputs'], AdaptiveSettings(**entry['adaptive']))
            dump(directory / 'host.json', host_interval(before_host, host_snapshot(switches)))
            manifest['cases'].append(result); dump(out / 'manifest.json', manifest)
            print(json.dumps({'case': name, 'status': result['status'], 'observation_slots': result['observation_slots'],
                              'completed_observations': result['completed_observations'], 'updates': result['successful_updates']}), flush=True)
            if result['status'] != 'passed': raise RuntimeError('工程案例失败: ' + name)
        manifest['status'] = 'passed'
    except Exception as exc:
        manifest.update(status='failed', error=str(exc), traceback=traceback.format_exc()); print(traceback.format_exc(), file=sys.stderr)
    finally:
        for client in clients.values(): client.close()
        if network: network.stop()
        if rpc_handle: finish_capture(rpc_handle)
        jsonlines(out / 'rpc-operations.jsonl', rpc_log)
        manifest.update(end_utc=datetime.now(timezone.utc).isoformat()); manifest['host']['load_end'] = list(os.getloadavg())
        manifest['artifact_sha256'] = {str(p.relative_to(out)): sha(p) for p in out.rglob('*') if p.is_file()
            and p.name != 'manifest.json' and 'executed-source' not in p.parts and 'launch' not in p.parts}
        dump(out / 'manifest.json', manifest)
    return int(manifest['status'] != 'passed')


if __name__ == '__main__': raise SystemExit(main())
