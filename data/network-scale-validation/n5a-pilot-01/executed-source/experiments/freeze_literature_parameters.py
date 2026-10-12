"""从已经独立验收的完整矩阵冻结全局候选或最终参数。"""
import argparse
import csv
import json
from pathlib import Path
import shutil

from literature_stage_contract import ROOT, sha, specification
from literature_selection import select


def freeze(analysis, checks, output):
    manifest = json.loads((analysis / 'manifest.json').read_text())
    verified = json.loads(checks.read_text())
    if verified['status'] != 'passed' or verified['analysis_manifest_sha256'] != sha(analysis / 'manifest.json'):
        raise ValueError('参数输入未通过绑定的独立分析')
    for name, digest in manifest['artifact_sha256'].items():
        if sha(analysis / name) != digest: raise ValueError('分析附件改变')
    plan = json.loads((analysis / 'stage-plan.json').read_text())
    if plan['stage'] not in ('training', 'calibration'): raise ValueError('不能从测试结果选参')
    with (analysis / 'summary.csv').open() as stream: rows = list(csv.DictReader(stream))
    candidates = None if plan['selection'] is None else plan['selection']['selected_indices']
    chosen = select(rows, plan['stage'], specification(), candidates)
    output.mkdir(parents=True, exist_ok=False)
    write = lambda name, value: (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    write('selection.json', chosen); write('score-inputs.json', rows)
    shutil.copyfile(checks, output / 'analysis-checks.json')
    shutil.copyfile(ROOT / 'experiments/literature_selection.py', output / 'selection-source.py')
    record = dict(status=chosen['status'], stage=plan['stage'], analysis_manifest_sha256=sha(analysis / 'manifest.json'),
        analysis_checks_sha256=sha(checks), training_selected_indices=candidates,
        analysis_summary_sha256=sha(analysis / 'summary.csv'), global_parameters=True,
        artifact_sha256={p.name: sha(p) for p in output.iterdir() if p.is_file()})
    write('manifest.json', record)
    print(json.dumps(dict(status=chosen['status'], selected_indices=chosen['selected_indices'], cases=len(rows))))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('analysis', 'checks', 'output'): p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args(); freeze(a.analysis, a.checks, a.output)
