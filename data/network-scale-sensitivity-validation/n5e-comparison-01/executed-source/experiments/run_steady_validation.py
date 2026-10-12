#!/usr/bin/env python3
"""以显式交换机路径执行既有检查，可选仅进程内的日历时钟跳变。"""
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

ROOT=Path(__file__).resolve().parents[1]
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--format',choices=['common','compact','task','prefix'],default='common')
    p.add_argument('--ordinary',action='store_true')
    p.add_argument('--cases',nargs='+')
    p.add_argument('--inject-realtime-jumps',action='store_true')
    p.add_argument('--legacy-control',action='store_true')
    a=p.parse_args()
    if os.geteuid(): p.error('需要 sudo 运行')
    build=a.build.resolve(); output=a.output.resolve()
    if output.exists(): p.error('运行目录已经存在')
    m=json.loads((build/'manifest.json').read_text())
    if m['status']!='passed': p.error('构建未通过')
    normal=Path(shutil.which('simple_switch')).resolve()
    binary=normal if a.legacy_control else build/'install/bin/simple_switch'
    if not a.legacy_control and sha(binary)!=m['binary_sha256']: p.error('二进制摘要不符')
    if a.legacy_control and not a.inject_realtime_jumps: p.error('旧版本仅用于跳变阳性对照')
    launch=build/('launch-'+output.name)
    launch.mkdir(exist_ok=False)
    log=launch/'clock-jumps.txt'
    environment=os.environ.copy()
    environment['PATH']=str(launch)+':'+environment['PATH']
    script='#!/bin/sh\n'
    if a.inject_realtime_jumps:
        library=launch/'clock_jump.so'
        subprocess.run(['cc','-std=c11','-shared','-fPIC','-O2',str(ROOT/'experiments/clock_jump.c'),
                        '-ldl','-pthread','-o',str(library)],check=True)
        script+='export LD_PRELOAD='+shlex.quote(str(library))+'\n'
        script+='export TT_CLOCK_JUMP_LOG='+shlex.quote(str(log))+'\n'
    script+='exec '+shlex.quote(str(binary))+' "$@"\n'
    (launch/'simple_switch').write_text(script)
    (launch/'simple_switch').chmod(0o755)
    # Python CLI 保持原安装，Thrift 接口没有改动，PATH 只替换目标程序。
    command=[sys.executable,str(ROOT/'experiments/run_strong_validation.py'),
             '--format',a.format,'--output',str(output)]
    if a.ordinary: command.append('--ordinary')
    if a.cases: command+=['--cases',*a.cases]
    started=datetime.now(timezone.utc).isoformat()
    # 复用既有检查器，只替换连接计数类。每条 RPC 仍执行同一 Thrift 方法。
    sys.path.insert(0,str(ROOT/'experiments'))
    sys.path.insert(0,str(ROOT/'src'))
    import run_strong_validation as original
    from control.metered import MeteredClient
    original.Client=MeteredClient
    os.environ.update(environment)
    previous_argv=sys.argv
    try:
        sys.argv=command[1:]
        returncode=original.main()
    finally:
        sys.argv=previous_argv
    if not (output/'manifest.json').exists(): raise RuntimeError('没有生成运行清单')
    summary=json.loads((output/'manifest.json').read_text())
    # 公开摘要只保留抽象路径，原始构建清单另以哈希标识。
    public_build=json.loads(json.dumps(m).replace(str(ROOT),'<project>'))
    info={'build':public_build,'build_manifest_sha256':sha(build/'manifest.json'),
          'actual_binary_sha256':sha(binary),'legacy_positive_control':a.legacy_control,
          'realtime_jumps_in_switch_only':a.inject_realtime_jumps,'start_utc':started,
          'clock_jump_library_sha256':sha(launch/'clock_jump.so') if a.inject_realtime_jumps else None,
          'clock_jump_source_sha256':sha(ROOT/'experiments/clock_jump.c'),
          'wrapper_sha256':sha(Path(__file__)), 'host_realtime_modified':False,
          'injected_events':[]}
    if log.exists():
        info['injected_events']=[{'process_id':int(pid),'elapsed_in_process_ns':int(t),'realtime_shift_seconds':int(v)}
                                 for pid,t,v in (line.split() for line in log.read_text().splitlines())]
    if a.inject_realtime_jumps and not info['injected_events']:
        summary['status']='failed'; summary['clock_injection_failure']='未观察到真实日历接口跳变'
    summary['runtime_clock']=info
    archive=output/'executed-source/experiments'
    for name in ['experiments/run_steady_validation.py','experiments/clock_jump.c','src/control/metered.py']:
        target=output/'executed-source'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,target)
        summary['source_sha256'][name]=sha(ROOT/name)
    (output/'manifest.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    assert sha(normal)==m['original_binary_sha256'],'全局程序发生变化'
    return returncode or int(summary['status']!='passed')


if __name__=='__main__': raise SystemExit(main())
