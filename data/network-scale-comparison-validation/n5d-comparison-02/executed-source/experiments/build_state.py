"""独立编译扩展格式，保留原始最小核心及其数据版本。"""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def build(diagnostics=False):
    target = ROOT / 'build' / ('state_int_diagnostics.json' if diagnostics else 'state_int.json')
    target.parent.mkdir(exist_ok=True)
    command = ['p4c-bm2-ss', '--std', 'p4-16']
    if diagnostics:
        command += ['-DENABLE_DIAGNOSTICS=1']
    subprocess.run(command + ['-o', str(target), str(ROOT / 'src/p4/state_int.p4')], check=True)
    assert json.loads(target.read_text())['pipelines']
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostics', action='store_true')
    print(build(parser.parse_args().diagnostics))
