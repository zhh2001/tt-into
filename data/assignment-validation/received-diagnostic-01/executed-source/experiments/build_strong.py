"""编译固定路径参考机制及独立的格式消融。"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from collector.strong_codec import FORMATS


def build(name, diagnostics=True):
    fmt = FORMATS[name]
    target = ROOT / 'build' / f'strong_{name}_{"diagnostics" if diagnostics else "ordinary"}.json'
    target.parent.mkdir(exist_ok=True)
    command = ['p4c-bm2-ss', '--std', 'p4-16', f'-DWIRE_FORMAT={fmt["id"]}']
    if diagnostics: command.append('-DENABLE_DIAGNOSTICS=1')
    subprocess.run(command + ['-o', str(target), str(ROOT / 'src/p4/strong_int.p4')], check=True)
    assert json.loads(target.read_text())['pipelines']
    return target


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--format', choices=FORMATS, default='common')
    p.add_argument('--ordinary', action='store_true')
    a = p.parse_args()
    print(build(a.format, not a.ordinary))
