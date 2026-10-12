#!/usr/bin/env python3
"""编译普通版及带正确性诊断的 TT-INT。"""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def build(diagnostics: bool = False) -> Path:
    target = ROOT / "build" / ("tt_int_diagnostics.json" if diagnostics else "tt_int.json")
    target.parent.mkdir(exist_ok=True)
    command = ["p4c-bm2-ss", "--std", "p4-16"]
    if diagnostics:
        command.append("-DENABLE_DIAGNOSTICS=1")
    command += ["-o", str(target), str(ROOT / "src/p4/tt_int.p4")]
    subprocess.run(command, check=True)
    # 防止编译成功但产生空的或损坏的部署文件。
    assert json.loads(target.read_text())["pipelines"]
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostics", action="store_true")
    args = parser.parse_args()
    print(build(args.diagnostics))
