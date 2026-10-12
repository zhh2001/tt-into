"""先审计持久业务消费，再冻结256例及两次完整网络部署顺序。"""
import argparse
import json
from pathlib import Path
import shutil
from observer_comparison_contract import ROOT, METHOD_PATH, METHOD_SHA256, audit_consumption, plan_fields, sha, validate
from run_observer_comparison import SOURCES
from analyze_report_observer import write


def prepare(output):
    methods=METHOD_PATH.parent
    record=json.loads((methods/'manifest.json').read_text())
    if record['status']!='methods_frozen': raise ValueError('方法未冻结')
    for name,digest in record['artifact_sha256'].items():
        if sha(methods/name)!=digest: raise ValueError('冻结方法附件改变')
    digests={name:sha(ROOT/name) for name in SOURCES}
    audit=audit_consumption(ROOT/'data')
    plan=dict(plan_fields(audit),source_sha256=digests,
              scope='四条新业务、两个完整独立部署块、控制器主目标，不再选择参数')
    validate(plan,current=True)
    output.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(METHOD_PATH,output/'method-contract.json')
    for name,value in (('comparison-plan.json',plan),('seed-audit.json',audit),
        ('frozen-static.json',plan['common_initialization']),('protocol.json',plan['protocol']),
        ('execution-order.json',plan['execution_order'])): write(output/name,value)
    for name in SOURCES:
        target=output/'executed-source'/name; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,target)
    write(output/'manifest.json',dict(status='prepared',execution_ready=True,fresh_inputs_generated=False,
        fresh_business_seeds=plan['fresh_business_seeds'],expected_cases=256,
        method_contract_manifest_sha256=sha(methods/'manifest.json'),
        artifact_sha256={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}))
    print(json.dumps(dict(status='prepared',cases=256,fresh_business_seeds=plan['fresh_business_seeds'],
                         inputs_generated=False,network_started=False,execution_sources=len(SOURCES))))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',type=Path,required=True)
    prepare(p.parse_args().output)
