"""冻结完整阶段计划，校准依赖训练选择，比较前审计新业务。"""
import argparse
import json
from pathlib import Path
import shutil

from literature_stage_contract import ROOT, REFERENCE, fields, sha, specification, validate
from literature_selection import select
from run_literature_matrix import SOURCES
from network_comparison_contract import audit_consumption


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def read_selection(path, required_stage):
    manifest = json.loads((path.parent / 'manifest.json').read_text())
    for name, digest in manifest['artifact_sha256'].items():
        if sha(path.parent / name) != digest: raise ValueError('参数选择附件改变')
    chosen = json.loads(path.read_text())
    rows = json.loads((path.parent / 'score-inputs.json').read_text())
    candidates = manifest.get('training_selected_indices')
    expected = select(rows, required_stage, specification(), candidates)
    if chosen != expected: raise ValueError('全局选择与完整评分不符')
    return chosen


def prepare(stage, output, selection_path=None):
    if stage == 'training' and selection_path is not None: raise ValueError('训练不接受已选参数')
    required = 'training' if stage == 'calibration' else 'calibration'
    chosen = None if stage == 'training' else read_selection(selection_path, required)
    audit = audit_consumption(ROOT / 'data') if stage == 'comparison' else None
    plan = fields(stage, chosen, audit)
    output.mkdir(parents=True, exist_ok=False)
    method_contract = dict(reference=json.loads(REFERENCE.read_text()), selection=chosen,
                           literature_parameter_grids=plan['protocol']['parameter_grids'])
    write(output / 'method-contract.json', method_contract)
    plan.update(source_sha256={name: sha(ROOT / name) for name in SOURCES},
                method_contract_sha256=sha(output / 'method-contract.json'),
                selection_source_sha256=None if selection_path is None else sha(selection_path))
    validate(plan, current=True)
    for name, value in (('stage-plan.json', plan), ('frozen-static.json', plan['protocol']['profiles']),
                        ('protocol.json', plan['protocol']), ('seed-audit.json', audit),
                        ('execution-order.json', plan['execution_order'])):
        write(output / name, value)
    for name in SOURCES:
        target = output / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT / name, target)
    write(output / 'manifest.json', dict(status='prepared', execution_ready=True, stage=stage,
        fresh_inputs_generated=False, business_generated=False, business_seeds=plan['business_seeds'],
        expected_cases=plan['expected_cases'], artifact_sha256={str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}))
    print(json.dumps(dict(status='prepared', stage=stage, cases=plan['expected_cases'],
                         business_seeds=plan['business_seeds'], inputs_generated=False)))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', choices=('training', 'calibration', 'comparison'), required=True)
    p.add_argument('--output', type=Path, required=True); p.add_argument('--selection', type=Path)
    a = p.parse_args()
    if a.stage != 'training' and a.selection is None: p.error('后续阶段需要--selection')
    prepare(a.stage, a.output, a.selection)
