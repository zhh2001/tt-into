"""冻结十二配置三块共同观察静态确认，不生成业务。"""
import argparse
import json
from pathlib import Path
import shutil

from observer_confirmation_contract import ROOT, CANDIDATE_PATH, METHOD_PATH, TRAINING, plan_fields, sha, validate
from run_observer_confirmation import SOURCES
from analyze_report_observer import write


def prepare(output):
    audit = json.loads((TRAINING / 'seed-audit.json').read_text())
    plan = dict(plan_fields(), seed_audit=audit, source_sha256={n: sha(ROOT / n) for n in SOURCES},
                scope='固定十二静态配置、已消费双业务三次网络部署，共同控制器目标确认')
    validate(plan, current=True)
    output.mkdir(parents=True, exist_ok=False)
    for source, name in ((CANDIDATE_PATH, 'candidate-plan.json'), (METHOD_PATH, 'method-contract.json')):
        shutil.copyfile(source, output / name)
    for name, value in (('confirmation-plan.json', plan), ('shortlist.json', plan['shortlist']), ('protocol.json', plan['protocol']), ('seed-audit.json', audit)):
        write(output / name, value)
    for name in SOURCES:
        target = output / 'executed-source' / name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    write(output / 'manifest.json', {'status': 'prepared', 'business_generated': False, 'expected_cases': 72,
        'artifact_sha256': {str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}})
    print(json.dumps({'status': 'prepared', 'cases': 72, 'inputs_generated': False, 'network_started': False}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', type=Path, required=True)
    prepare(p.parse_args().output)
