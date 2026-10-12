"""执行冻结六组配置的三块静态确认，保留每例原始仪表。"""
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
from run_into_validation import (ROOT, ROUTES, IntoClient, build, configure, create_fork, dump,
    finish_capture, jsonlines, software_version)
from run_matched_reference import AdaptiveSettings, matched_management, measured_fees, settings, sha
from confirmation_contract import execution_order, validate_plan, ORDER_SEEDS, FROZEN_PLAN_SHA256
from run_network_static import SOURCES as STATIC_SOURCES, do_case, make_inputs
from network_host import snapshot as host_snapshot, interval as host_interval

SOURCES = tuple(dict.fromkeys(STATIC_SOURCES + (
    'experiments/prepare_static_confirmation.py', 'experiments/confirmation_contract.py',
    'experiments/run_static_confirmation.py', 'experiments/check_static_confirmation.py',
    'experiments/analyze_static_confirmation.py', 'experiments/check_confirmation_analysis.py',
    'experiments/confirmation_repeats.py', 'experiments/diagnose_static_transfer.py',
    'tests/test_static_confirmation.py')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plan', type=Path, default=ROOT / 'data/network-static-plan/n3f-confirmation-01/confirmation-plan.json')
    a = parser.parse_args()
    if os.geteuid():
        parser.error('需要 sudo 运行')
    plan = json.loads(a.plan.read_text())
    if sha(a.plan) != FROZEN_PLAN_SHA256:
        raise ValueError('冻结候选文件摘要不同，不重新选择候选')
    model = ROOT / 'data/matched-reference/n3b-matched-01'
    validate_plan(plan, a.plan.parent, ROOT / 'data/network-static-validation/n3e-static-01',
                  ROOT / 'data/network-static-diagnostics/n3e-transfer-01')
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
    reference = measured_fees(ROOT / 'data/into-validation/n3-validation-01')
    management = matched_management(reference)
    inputs = {}
    order = execution_order(plan)
    for i, entry in enumerate(order):
        s, b, method = entry['scenario'], entry['budget_level'], entry['method']
        name = entry['name']
        inputs[name] = dict(entry, inputs=make_inputs(s, entry['seed'], i * 200))
        if len(inputs[name]['inputs'][0]) > 200 or i * 200 + len(inputs[name]['inputs'][0]) > 65536:
            raise ValueError('离线 IPv4 ID 验证仪表空间不足')
    manifest = {'run_id': out.name, 'status': 'running', 'start_utc': datetime.now(timezone.utc).isoformat(),
        'scope': '有限静态确认，六配置、两已消费业务、三部署块；非新测试或统计置信区间',
        'smoke': False, 'training_seeds': [11, 29], 'deployment_blocks': 3,
        'within_order_seeds': list(ORDER_SEEDS), 'confirmation_plan_sha256': sha(a.plan),
        'execution_order': order, 'expected_cases': len(inputs), 'cases': [], 'settings': settings(),
        'topology': {'routes': [{'flow': f, 'objects': r} for f, r in ROUTES.items()]},
        'software': {'python': sys.version, 'p4c': software_version(['p4c-bm2-ss', '--version']),
            'bmv2': software_version([str(binary), '--version']), 'mininet': software_version(['mn', '--version'])},
        'host': {'cpu_count': os.cpu_count(), 'load_start': list(os.getloadavg())},
        'steady_binary_sha256': digest, 'p4_json_sha256': sha(program),
        'source_sha256': {n: sha(ROOT / n) for n in SOURCES},
        'model_reference': {n: sha(model / n) for n in ('manifest.json', 'training-selections.json', 'frozen-parameters.json')},
        'rpc_reference': reference, 'parameter_selection': '只采用冻结候选，不进行动态参数选择',
        'timing': '实际单调截止、CPU 求解、同步 RPC，迟到整周期跳过；无模型时延替代',
        'observation': '实收身份/计数/确认/自有完成费用；发送与抓包只离线验证'}
    for n in SOURCES:
        p = out / 'executed-source' / n
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / n, p)
    shutil.copyfile(program, out / 'compiled-program.json')
    shutil.copyfile(a.plan, out / 'confirmation-plan.json')
    dump(out / 'frozen-inputs.json', inputs)
    dump(out / 'execution-order.json', order)
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
        network, source, destination, collector, switches = create_fork(program, out, prefix='ts', base_port=9560)
        clients = {n: IntoClient(s.thrift_port, n, rpc_log) for n, s in enumerate(switches, 1)}
        configure(clients, switches)
        for i, (name, config) in enumerate(inputs.items()):
            directory = out / name
            directory.mkdir()
            s, b, method = config['scenario'], config['budget_level'], config['method']
            initial = config['config']
            before_host = host_snapshot(switches)
            result = do_case(name, directory, i, s, b, config['seed'], method, initial, AdaptiveSettings(), management,
                             source, destination, collector, clients, rpc_log, *config['inputs'])
            dump(directory / 'host.json', host_interval(before_host, host_snapshot(switches)))
            result.update(block=config['block'], order_seed=config['order_seed'], position=i,
                          candidate_index=config['candidate_index'], roles=config['roles'],
                          scope='六配置有限静态重复确认，训练业务 11/29，非新业务测试')
            dump(directory / 'result.json', result)
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
