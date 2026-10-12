"""执行冻结的三块静态候选确认，复用已验收的真实业务入口。"""
import argparse
from dataclasses import replace
from datetime import datetime,timezone
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
from run_into_validation import ROOT,IntoClient,build,dump,finish_capture,jsonlines,software_version
from run_matched_reference import AdaptiveSettings,matched_management,measured_fees
from scale_confirmation_execution import validate,sha
from scale_static_case import do_case
from scalable_topology import create_network,configure
from scale_training_workload import inputs as make_inputs
from orchestration.network_spec import FixedNetwork
from network_host import snapshot as host_snapshot,interval as host_interval


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--validate-only',action='store_true');a=p.parse_args()
    if os.geteuid() and not a.validate_only:p.error('需要sudo运行')
    plan=json.loads(a.plan.read_text());frozen=json.loads((a.plan.parent/'manifest.json').read_text())
    for name,digest in frozen['artifact_sha256'].items():
        if sha(a.plan.parent/name)!=digest:raise ValueError('执行输入摘要改变 '+name)
    validate(plan,True,a.output)
    if a.validate_only:
        print(json.dumps(dict(status='validated',cases=plan['expected_cases'],batches=len(plan['batches']),business_generated=False)));return 0
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    setLogLevel('warning');program=build(False)
    binary=ROOT/'build/bmv2-steady-01/install/bin/simple_switch'
    if sha(binary)!=json.loads((binary.parents[2]/'manifest.json').read_text())['binary_sha256']:raise ValueError('单调交换机摘要不符')
    launch=out/'launch';launch.mkdir();wrapper=launch/'simple_switch'
    wrapper.write_text('#!/bin/sh\nexec '+shlex.quote(str(binary))+' "$@"\n');wrapper.chmod(0o755)
    os.environ['PATH']=str(launch)+':'+os.environ['PATH']
    manifest=dict(run_id=out.name,status='running',start_utc=datetime.now(timezone.utc).isoformat(),
        mode=plan['mode'],expected_cases=plan['expected_cases'],batches=[],business_seeds=plan['business_seeds'],
        independent_test=False,plan_sha256=sha(a.plan),source_sha256=plan['source_sha256'],
        steady_binary_sha256=sha(binary),p4_json_sha256=sha(program),
        software=dict(python=sys.version,p4c=software_version(['p4c-bm2-ss','--version']),bmv2=software_version([str(binary),'--version']),mininet=software_version(['mn','--version'])),
        host=dict(cpu_count=os.cpu_count(),load_start=list(os.getloadavg())),
        concurrent_activity='主业务期间无同宿主回归、分析、绘图或第二网络')
    for n in plan['source_sha256']:
        target=out/'executed-source'/n;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,target)
    shutil.copyfile(program,out/'compiled-program.json');shutil.copyfile(a.plan,out/'stage-plan.json')
    shutil.copyfile(a.plan.parent/'training-input.json',out/'training-input.json')
    shutil.copyfile(a.plan.parent/'confirmation-input.json',out/'confirmation-input.json');dump(out/'manifest.json',manifest)
    pattern='test_scale_confirmation.py' if plan['mode']=='smoke' else 'test*.py'
    manifest['regression_scope']='本阶段专项' if plan['mode']=='smoke' else '完整单元回归'
    regression=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p',pattern,'-v'],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (out/'unit-tests.txt').write_text(regression.stdout)
    if regression.returncode:
        manifest.update(status='failed',error='启动回归失败');dump(out/'manifest.json',manifest);return 1
    completed=0
    try:
        fees=measured_fees(ROOT/'data/into-validation/n3-validation-01')
        for batch in plan['batches']:
            b=out/batch['batch_id'];b.mkdir();spec=FixedNetwork(plan['networks'][batch['network_id']])
            record=dict(batch_id=batch['batch_id'],block=batch['block'],network_id=batch['network_id'],status='starting',cases=[],started_monotonic_ns=time.monotonic_ns())
            manifest['batches'].append(record);dump(out/'manifest.json',manifest);dump(b/'manifest.json',record)
            network=None;clients={};handle=None;rpc=[]
            try:
                ready,stop=b/'rpc.ready',b/'rpc.stop';log=(b/'rpc-capture.log').open('w')
                proc=subprocess.Popen([sys.executable,str(ROOT/'experiments/strong_rpc.py'),'--events',str(b/'rpc.jsonl'),'--pcap',str(b/'rpc.pcap'),
                    '--ready',str(ready),'--stop',str(stop),'--ports',*[str(9680+n) for n in spec.nodes]],stdout=log,stderr=subprocess.STDOUT)
                handle=proc,stop,log;deadline=time.monotonic()+5
                while not ready.exists():
                    if proc.poll() is not None or time.monotonic()>deadline:raise RuntimeError('控制捕获未就绪')
                    time.sleep(.01)
                staging=b/('network-'+batch['network_id']);staging.mkdir()
                network,hosts,switches=create_network(spec,program,staging,prefix='st',base_port=9680)
                clients={n:IntoClient(s.thrift_port,n,rpc) for n,s in switches.items()};configure(spec,clients,switches)
                record.update(status='running',switch_pids=[s.process.pid for s in switches.values()]);dump(b/'manifest.json',record)
                management=replace(matched_management(fees),routes=spec.routes,links=spec.links)
                for local,entry in enumerate(batch['entries']):
                    directory=b/entry['name'];directory.mkdir()
                    frozen_inputs=make_inputs(spec,entry['scenario'],entry['business_seed'],local*1000)
                    before=host_snapshot(list(switches.values()))
                    try:
                        result=do_case(entry,spec,directory,management,hosts['source'],hosts['destination'],hosts['collector'],clients,rpc,
                            frozen_inputs,AdaptiveSettings(**entry['adaptive']))
                    except Exception:
                        result=json.loads((directory/'result.json').read_text())
                        if result.get('status')!='failed':raise
                        result['rpc_stop_index']=len(rpc);dump(directory/'result.json',result)
                    host=host_interval(before,host_snapshot(list(switches.values())))
                    host['scope']='案例安装至核对完成，CPU及RSS端点，非硬件性能';dump(directory/'host.json',host)
                    record['cases'].append(result);completed+=1
                    dump(b/'manifest.json',record);dump(out/'manifest.json',manifest)
                    print(json.dumps(dict(completed=completed,total=plan['expected_cases'],batch=batch['batch_id'],case=entry['name'],status=result['status'],reports=result.get('report_count'))),flush=True)
                record['status']='passed' if all(c['status']=='passed' for c in record['cases']) else 'completed_with_failures'
            finally:
                for client in clients.values():client.close()
                if network:network.stop()
                if handle:finish_capture(handle)
                jsonlines(b/'rpc-operations.jsonl',rpc)
                record.update(cleaned_monotonic_ns=time.monotonic_ns(),artifact_sha256={str(q.relative_to(b)):sha(q) for q in b.rglob('*') if q.is_file() and q.name!='manifest.json'})
                dump(b/'manifest.json',record);dump(out/'manifest.json',manifest)
        manifest['status']='passed' if all(b['status']=='passed' for b in manifest['batches']) else 'completed_with_failures'
    except Exception as exc:
        manifest.update(status='failed',error=str(exc),traceback=traceback.format_exc());print(traceback.format_exc(),file=sys.stderr)
    finally:
        manifest.update(completed_cases=completed,end_utc=datetime.now(timezone.utc).isoformat());manifest['host']['load_end']=list(os.getloadavg())
        manifest['artifact_sha256']={str(q.relative_to(out)):sha(q) for q in out.rglob('*') if q.is_file() and q!=out/'manifest.json' and 'executed-source' not in q.parts and 'launch' not in q.parts}
        dump(out/'manifest.json',manifest)
    return int(manifest['status']=='failed')


if __name__=='__main__':raise SystemExit(main())
