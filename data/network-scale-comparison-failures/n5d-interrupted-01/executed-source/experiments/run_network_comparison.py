"""执行参数固定、四个独立业务与两部署块的完整比较。"""
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
from network_comparison_contract import validate_plan, ORDER_SEED
from run_network_static import do_case, make_inputs
from run_dynamic_network_calibration import SOURCES as CALIBRATION_SOURCES
from network_host import snapshot as host_snapshot, interval as host_interval

SOURCES = tuple(dict.fromkeys(CALIBRATION_SOURCES + (
    'experiments/prepare_network_comparison.py', 'experiments/network_comparison_contract.py',
    'experiments/prepare_network_comparison_execution.py', 'experiments/run_network_comparison.py',
    'experiments/check_network_comparison.py', 'experiments/analyze_network_comparison.py',
    'experiments/network_comparison_summary.py', 'experiments/check_network_comparison_analysis.py',
    'experiments/network_comparison_pairs.py', 'tests/test_network_comparison.py')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plan', type=Path, default=ROOT / 'data/network-comparison-plan/n3h-execution-02/comparison-plan.json')
    parser.add_argument('--validate-only', action='store_true', help='只检查合同、来源与完整业务输入，不启动网络')
    a = parser.parse_args()
    if os.geteuid() and not a.validate_only:
        parser.error('需要 sudo 运行')
    plan = json.loads(a.plan.read_text())
    plan_record = json.loads((a.plan.parent / 'manifest.json').read_text())
    if sha(a.plan) != plan_record['artifact_sha256']['comparison-plan.json']:
        raise ValueError('完整比较清单摘要不同')
    validate_plan(plan, a.plan.parent, check_current=True)
    order = plan['execution_order']
    if a.validate_only:
        print(json.dumps({'status': 'validated', 'cases': len(order),
                          'business_count': 4, 'deployment_blocks': 2, 'inputs_generated': False}))
        return 0
    model = ROOT / 'data/matched-reference/n3b-matched-01'
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
    inputs = {}
    attempted = []
    for i, entry in enumerate(order):
        s, b, method = entry['scenario'], entry['budget_level'], entry['method']
        name = entry['name']
        if entry['seed'] not in attempted:
            attempted.append(entry['seed'])
            dump(out / 'generation-attempts.json', {'fresh_business_seeds': attempted, 'status': 'generation_started',
                'scope': '新业务生成调用前落盘，生成异常仍保守视为已消费，不重称未见业务'})
        inputs[name] = dict(entry, inputs=make_inputs(s, entry['seed'], i * 200))
        if len(inputs[name]['inputs'][0]) > 200 or i * 200 + len(inputs[name]['inputs'][0]) > 65536:
            raise ValueError('离线 IPv4 ID 验证仪表空间不足')
    reference = measured_fees(ROOT / 'data/into-validation/n3-validation-01')
    management = matched_management(reference)
    manifest = {'run_id': out.name, 'status': 'running', 'start_utc': datetime.now(timezone.utc).isoformat(),
        'scope': '256 例独立业务比较，四业务两部署块，参数和评价先冻结',
        'smoke': False, 'training_seeds': [11, 29], 'calibration_seeds': [1301, 1423],
        'fresh_business_seeds': plan['fresh_business_seeds'], 'fresh_inputs_generated': True, 'business_count': 4, 'deployment_blocks': 2,
        'order_seed': ORDER_SEED, 'comparison_plan_sha256': sha(a.plan), 'method_contract_sha256': plan['method_contract_sha256'],
        'execution_order': order, 'expected_cases': len(inputs), 'cases': [], 'settings': settings(),
        'topology': {'routes': [{'flow': f, 'objects': r} for f, r in ROUTES.items()]},
        'software': {'python': sys.version, 'p4c': software_version(['p4c-bm2-ss', '--version']),
            'bmv2': software_version([str(binary), '--version']), 'mininet': software_version(['mn', '--version'])},
        'host': {'cpu_count': os.cpu_count(), 'load_start': list(os.getloadavg())},
        'steady_binary_sha256': digest, 'p4_json_sha256': sha(program),
        'source_sha256': {n: sha(ROOT / n) for n in SOURCES},
        'model_reference': {n: sha(model / n) for n in ('manifest.json', 'training-selections.json', 'frozen-parameters.json')},
        'rpc_reference': reference, 'parameter_selection': 'final_frozen，固定P03/P02/P04，不根据测试调整方法或预算',
        'timing': '实际单调截止、CPU 求解、同步 RPC，迟到整周期跳过；无模型时延替代',
        'observation': '实收身份/计数/确认/自有完成费用；发送与抓包只离线验证'}
    for n in SOURCES:
        p = out / 'executed-source' / n
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / n, p)
    shutil.copyfile(program, out / 'compiled-program.json')
    shutil.copyfile(a.plan, out / 'comparison-plan.json')
    shutil.copyfile(a.plan.parent / 'method-contract.json', out / 'method-contract.json')
    shutil.copyfile(a.plan.parent / 'seed-audit.json', out / 'seed-audit.json')
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
            initial = config['initial_config']
            before_host = host_snapshot(switches)
            result = do_case(name, directory, i, s, b, config['seed'], method, initial, AdaptiveSettings(**config['adaptive']), management,
                             source, destination, collector, clients, rpc_log, *config['inputs'])
            dump(directory / 'host.json', host_interval(before_host, host_snapshot(switches)))
            result.update(order_seed=config['order_seed'], position=i, block=config['block'], parameter_index=config['parameter_index'],
                          scope='冻结参数的独立业务比较，保留全部任务与失败')
            options = json.loads((directory / 'algorithm-settings.json').read_text())
            if options['adaptive'] != config['adaptive']:
                raise ValueError('实际参数不同于冻结清单')
            options.update(model_parameters_frozen=False, network_parameters_frozen=True, final_frozen=True,
                           network_parameter_index=config['parameter_index'],
                           method_contract_sha256=plan['method_contract_sha256'],
                           parameter_origin='N3g全局参数与N3h方法合同冻结，无测试调参')
            dump(directory / 'algorithm-settings.json', options)
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
