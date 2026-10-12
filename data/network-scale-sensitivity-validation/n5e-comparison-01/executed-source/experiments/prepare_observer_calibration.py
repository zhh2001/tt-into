"""冻结184例共同观察动态校准，不生成业务。"""
import argparse
import json
from pathlib import Path
import shutil
from observer_calibration_contract import ROOT,METHOD_PATH,plan_fields,sha,validate
from run_observer_calibration import SOURCES
from analyze_report_observer import write


def prepare(output):
    plan=dict(plan_fields(),source_sha256={n:sha(ROOT/n) for n in SOURCES},
              scope='共同100ms报告入口，184例固定六预设动态全局校准')
    validate(plan,current=True)
    output.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(METHOD_PATH,output/'method-contract.json')
    for name,value in (('calibration-plan.json',plan),('frozen-static.json',plan['common_initialization']),('protocol.json',plan['protocol'])):
        write(output/name,value)
    for name in SOURCES:
        target=output/'executed-source'/name; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,target)
    write(output/'manifest.json',dict(status='prepared',business_generated=False,expected_cases=184,
        artifact_sha256={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}))
    print(json.dumps(dict(status='prepared',cases=184,inputs_generated=False,network_started=False)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',type=Path,required=True)
    prepare(p.parse_args().output)
