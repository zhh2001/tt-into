"""编译独立版本 6，不修改旧数据平面或产物。"""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def build(diagnostics=False):
    target = ROOT / 'build' / f'into_{"diagnostics" if diagnostics else "ordinary"}.json'
    target.parent.mkdir(exist_ok=True)
    args = ['p4c-bm2-ss', '--std', 'p4-16']
    if diagnostics:
        args.append('-DENABLE_DIAGNOSTICS=1')
    subprocess.run(args + ['-o', str(target), str(ROOT / 'src/p4/into_int.p4')], check=True)
    if not json.loads(target.read_text())['pipelines']:
        raise ValueError('编译产物没有流水线')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostics', action='store_true')
    print(build(parser.parse_args().diagnostics))
