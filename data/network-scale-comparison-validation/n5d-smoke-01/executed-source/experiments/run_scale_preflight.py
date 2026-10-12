"""执行八采样节点八流合流拓扑的实际BMv2正确性矩阵。"""
import argparse
from dataclasses import replace
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
from run_into_validation import ROOT, IntoClient, build, dump, finish_capture, jsonlines, software_version
from run_matched_reference import AdaptiveSettings, matched_management, measured_fees, settings
from scale_preflight_contract import validate, validate_paths, sha
from scale_preflight_case import do_case
from scalable_topology import create_network, configure
from scalable_workload import inputs as make_inputs
from orchestration.network_spec import FixedNetwork
from network_host import snapshot as host_snapshot, interval as host_interval


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--validate-only', action='store_true'); a = p.parse_args()
    if os.geteuid() and not a.validate_only: p.error('需要sudo运行')
    plan = json.loads(a.plan.read_text()); frozen = json.loads((a.plan.parent / 'manifest.json').read_text())
    if sha(a.plan) != frozen['artifact_sha256']['stage-plan.json']: raise ValueError('准备清单摘要改变')
    validate(plan, current=True)
    validate_paths(plan, a.output)
    if a.validate_only:
        print(json.dumps(dict(status='validated', cases=6, inputs_generated=False, network_started=False))); return 0
    out = a.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    setLogLevel('warning'); program = build(False)
    binary = ROOT / 'build/bmv2-steady-01/install/bin/simple_switch'
    if sha(binary) != json.loads((binary.parents[2] / 'manifest.json').read_text())['binary_sha256']: raise ValueError('单调时钟交换机摘要不符')
    launch = out / 'launch'; launch.mkdir(); wrapper = launch / 'simple_switch'
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(binary)) + ' "$@"\n'); wrapper.chmod(0o755)
    os.environ['PATH'] = str(launch) + ':' + os.environ['PATH']
    inputs = {e['name']: make_inputs(FixedNetwork(plan['networks'][e['network_id']]), e['method'], i * 1000)
              for i, e in enumerate(plan['execution_order'])}
    manifest = dict(run_id=out.name, status='running', start_utc=datetime.now(timezone.utc).isoformat(),
        expected_cases=6, cases=[], networks=plan['networks'], deployments=[], execution_order=plan['execution_order'],
        business_seeds=[], independent_test=False, observation_period_us=100000, decision_period_us=1000000,
        plan_sha256=sha(a.plan), source_sha256=plan['source_sha256'], steady_binary_sha256=sha(binary), p4_json_sha256=sha(program),
        software=dict(python=sys.version, p4c=software_version(['p4c-bm2-ss', '--version']),
            bmv2=software_version([str(binary), '--version']), mininet=software_version(['mn', '--version'])),
        host=dict(cpu_count=os.cpu_count(), load_start=list(os.getloadavg())),
        concurrent_activity='主业务期间不并发同宿主回归、分析、绘图或第二网络',
        scope='固定合流链通用入口正确性，不作性能排名')
    for n in plan['source_sha256']:
        target = out / 'executed-source' / n; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT / n, target)
    shutil.copyfile(program, out / 'compiled-program.json'); shutil.copyfile(a.plan, out / 'stage-plan.json')
    dump(out / 'frozen-inputs.json', inputs); dump(out / 'manifest.json', manifest)
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
            *[str(9680 + n) for n in range(1, 10)]], stdout=log, stderr=subprocess.STDOUT)
        rpc_handle = process, stop, log; deadline = time.monotonic() + 5
        while not ready.exists():
            if process.poll() is not None or time.monotonic() > deadline: raise RuntimeError('控制捕获未就绪')
            time.sleep(.01)
        fees = measured_fees(ROOT / 'data/into-validation/n3-validation-01')
        for ni, (network_id, topology) in enumerate(plan['networks'].items()):
            spec = FixedNetwork(topology); staging = out / ('network-' + network_id); staging.mkdir()
            deployment = dict(network_id=network_id, started_monotonic_ns=time.monotonic_ns(), status='starting')
            manifest['deployments'].append(deployment); dump(out / 'manifest.json', manifest)
            network, hosts, switches = create_network(spec, program, staging, prefix=f'sp{ni}', base_port=9680)
            clients = {n: IntoClient(s.thrift_port, n, rpc_log) for n, s in switches.items()}
            configure(spec, clients, switches)
            deployment.update(status='running', switch_pids=[s.process.pid for s in switches.values()])
            management = replace(matched_management(fees), routes=spec.routes, links=spec.links)
            for entry in plan['execution_order']:
                if entry['network_id'] != network_id: continue
                directory = out / entry['name']; directory.mkdir(); before = host_snapshot(list(switches.values()))
                result = do_case(entry, spec, directory, management, hosts['source'], hosts['destination'], hosts['collector'],
                                 clients, rpc_log, inputs[entry['name']], AdaptiveSettings(**entry['adaptive']))
                host = host_interval(before, host_snapshot(list(switches.values())))
                host['scope'] = '案例安装至核对完成，控制器及实际交换机进程，RSS端点非峰值，不包括采集/发包子进程CPU'
                dump(directory / 'host.json', host)
                manifest['cases'].append(result); dump(out / 'manifest.json', manifest)
                print(json.dumps(dict(case=entry['name'], status=result['status'], packets=result['packet_count'],
                    reports=result['report_count'], updates=result['successful_updates'])), flush=True)
            for client in clients.values(): client.close()
            clients = {}; network.stop(); network = None
            if any(s.process.poll() is None for s in switches.values()): raise RuntimeError('交换机未停止')
            deployment.update(status='passed', cleaned_monotonic_ns=time.monotonic_ns(),
                              switch_returncodes=[s.process.returncode for s in switches.values()])
            dump(out / 'manifest.json', manifest)
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
