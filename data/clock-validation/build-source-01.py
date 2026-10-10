#!/usr/bin/env python3
"""从固定上游源码构建项目独立的单调时钟 simple_switch。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '2bdd0b7b2b2ae89faf2720f2158e9842bc6d2dd2'
URL = f'https://codeload.github.com/p4lang/behavioral-model/tar.gz/{COMMIT}'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--jobs', type=int, default=2)
    a = p.parse_args()
    if a.jobs < 1 or a.jobs > 8:
        p.error('jobs 须为 1 至 8')
    output = a.output.resolve()
    if ROOT / 'build' not in output.parents:
        p.error('构建须放在项目 build/ 下')
    output.mkdir(parents=True, exist_ok=False)
    source, prefix = output/'source', output/'install'
    patch = ROOT/'experiments/patches/bmv2-steady-clock.patch'
    upstream_binary = Path(shutil.which('simple_switch')).resolve()
    manifest = {'status': 'running', 'upstream_commit': COMMIT, 'upstream_url': URL,
                'start_utc': datetime.now(timezone.utc).isoformat(),
                'patch_sha256': digest(patch), 'script_sha256': digest(Path(__file__)),
                'original_binary_sha256': digest(upstream_binary), 'jobs': a.jobs,
                'timestamp_unit': 'microseconds since each switch starts; 48-bit P4 field',
                'elapsed_clock': 'std::chrono::steady_clock',
                'wall_epoch_api': 'std::chrono::system_clock', 'commands': []}
    def save():
        (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    def run(command, cwd):
        manifest['commands'].append([str(x).replace(str(output), '<build>') for x in command])
        save()
        with (output/'build.log').open('a') as log:
            subprocess.run(command,cwd=cwd,stdout=log,stderr=subprocess.STDOUT,check=True,
                           env={**os.environ,'CXXFLAGS':'-O2 -g0','CFLAGS':'-O2 -g0'})
    try:
        save()
        archive=output/'upstream.tar.gz'
        urllib.request.urlretrieve(URL,archive)
        manifest['archive_sha256']=digest(archive)
        source.mkdir()
        with tarfile.open(archive) as tar:
            members=tar.getmembers()
            top=members[0].name.split('/')[0]
            tar.extractall(output,filter='data')
        extracted=output/top
        # source 仅为空的新目录，不覆盖任何已有构建或材料。
        source.rmdir()
        extracted.rename(source)
        touched=['targets/simple_switch/simple_switch.h', 'targets/simple_switch/simple_switch.cpp',
                 'include/bm/bm_sim/queueing.h']
        manifest['upstream_file_sha256']={n:digest(source/n) for n in touched}
        run(['patch','--batch','--forward','-p1','-i',str(patch)],source)
        manifest['patched_file_sha256']={n:digest(source/n) for n in touched}
        # tar 包没有 Git 描述，显式固定生成的版本字符串。
        (source/'VERSION').write_text('1.15.0-'+COMMIT[:8]+'-steady\n')
        run(['./autogen.sh'],source)
        run(['./configure','--prefix='+str(prefix),'--disable-tests','--without-pi',
             '--with-thrift','--with-nanomsg','--disable-dependency-tracking'],source)
        run(['make','-j'+str(a.jobs)],source)
        run(['make','install'],source)
        binary=prefix/'bin/simple_switch'
        manifest['binary_sha256']=digest(binary)
        manifest['version']=subprocess.check_output([str(binary),'--version'],text=True).strip()
        libraries=[]
        for line in subprocess.check_output(['ldd',str(binary)],text=True).splitlines():
            if '=>' not in line: continue
            name,location=line.split('=>',1)
            path=Path(location.strip().split()[0])
            if path.is_file(): libraries.append({'name':name.strip(),'sha256':digest(path),
                'scope':'project' if prefix in path.resolve().parents else 'system'})
        manifest['linked_libraries']=libraries
        assert digest(upstream_binary)==manifest['original_binary_sha256']
        manifest['status']='passed'
        print(json.dumps({'status':'passed','version':manifest['version'],
                          'binary_sha256':manifest['binary_sha256']},ensure_ascii=False))
    except Exception as exc:
        manifest.update(status='failed',failure=str(exc))
        raise
    finally:
        manifest['finish_utc']=datetime.now(timezone.utc).isoformat()
        save()


if __name__=='__main__': main()
