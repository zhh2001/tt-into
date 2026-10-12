"""校验器修复后重新审计并冻结完整独立业务比较，保留原中断记录。"""
import argparse
import json
from pathlib import Path
import shutil

from literature_stage_contract import ROOT, REFERENCE, fields, sha
from literature_comparison_execution_contract import validate
from prepare_literature_stage import read_selection
from run_literature_comparison import SOURCES
from network_comparison_contract import audit_consumption


def prepare(selection, failed, output):
    chosen = read_selection(selection, 'calibration')
    previous = json.loads((failed / 'manifest.json').read_text())
    if previous['status'] != 'failed': raise ValueError('需要完整保留的中断记录')
    plan = fields('comparison', chosen, audit_consumption(ROOT / 'data'))
    output.mkdir(parents=True, exist_ok=False)
    write = lambda name, value: (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    write('method-contract.json', dict(reference=json.loads(REFERENCE.read_text()), selection=chosen,
        literature_parameter_grids=plan['protocol']['parameter_grids']))
    plan.update(source_sha256={name: sha(ROOT / name) for name in SOURCES},
        method_contract_sha256=sha(output / 'method-contract.json'), selection_source_sha256=sha(selection),
        prior_failed_manifest_sha256=sha(failed / 'manifest.json'), prior_completed_cases=len(previous['cases']),
        retry_scope='修复独立校验器后完整重跑；方法、配置、预算和任务不调参；原中断数据保留')
    validate(plan, current=True)
    for name, value in (('stage-plan.json', plan), ('frozen-static.json', plan['protocol']['profiles']),
        ('protocol.json', plan['protocol']), ('seed-audit.json', plan['seed_audit']), ('execution-order.json', plan['execution_order'])):
        write(name, value)
    for name in SOURCES:
        target = output / 'executed-source' / name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    write('manifest.json', dict(status='prepared', execution_ready=True, fresh_inputs_generated=False,
        business_seeds=plan['business_seeds'], expected_cases=320,
        artifact_sha256={str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}))
    print(json.dumps(dict(status='prepared', cases=320, seeds=plan['business_seeds'], source_count=len(SOURCES))))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('selection', 'failed', 'output'): p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args(); prepare(a.selection, a.failed, a.output)
