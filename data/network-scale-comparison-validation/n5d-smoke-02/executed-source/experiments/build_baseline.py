"""编译共享门控与静态授权，保留此前数据平面的源码版本。"""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def build(diagnostics=False):
    name = 'baseline_int_diagnostics.json' if diagnostics else 'baseline_int.json'
    target = ROOT / 'build' / name
    target.parent.mkdir(exist_ok=True)
    command = ['p4c-bm2-ss', '--std', 'p4-16']
    if diagnostics:
        command += ['-DENABLE_DIAGNOSTICS=1']
    subprocess.run(command + ['-o', str(target), str(ROOT / 'src/p4/baseline_int.p4')], check=True)
    assert json.loads(target.read_text())['pipelines']
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostics', action='store_true')
    print(build(parser.parse_args().diagnostics))
