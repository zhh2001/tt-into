"""继承既有有限候选，冻结分批网络执行源码和完整顺序。"""
import argparse
import copy
import json
from pathlib import Path
from scalable_contract import ROOT,sha,source_files
from scale_training_contract import validate as validate_input
from scale_preflight_contract import validate_paths

INPUT='data/network-scale-plan/n5b-static-training-01/training-plan.json'
BATCH_SIZE=48
SMOKE=(('n3f2','stable','C00',11),('n8f2','stable','C08',11),('n5f8','shared_priority','C09',11))


def definition(mode='training'):
    source=ROOT/INPUT
    contract=json.loads(source.read_text());validate_input(contract)
    entries=copy.deepcopy(contract['execution_order'])
    if mode=='smoke':
        selected=[]
        for network,scenario,candidate,seed in SMOKE:
            row=next(e for e in entries if (e['network_id'],e['scenario'],e['candidate_id'],e['business_seed'])==(network,scenario,candidate,seed))
            row['training_position']=row['position'];row['position']=len(selected)
            selected.append(row)
        entries=selected
    elif mode!='training':raise ValueError('未知静态执行阶段')
    batches=[]
    for entry in entries:
        if not batches or batches[-1]['network_id']!=entry['network_id'] or len(batches[-1]['entries'])==BATCH_SIZE:
            batches.append(dict(batch_id=f'b{len(batches):02d}',network_id=entry['network_id'],entries=[]))
        batches[-1]['entries'].append(entry)
    return dict(stage='n5b_static_'+mode,status='prepared',execution_ready=True,
        training_input=dict(path=INPUT,sha256=sha(source)),mode=mode,
        expected_cases=len(entries),execution_order=entries,batches=batches,
        networks=contract['networks'],business_seeds=[11] if mode=='smoke' else [11,29],independent_test=False,
        observation_period_us=100000,decision_period_us=1000000,capacity=1,
        duration_us=10000000,measurement_window_us=[2000000,10000000],
        batch_contract=dict(max_cases=BATCH_SIZE,packet_id_base_per_case=1000,
            validation_identity_domain='批次内IPv4 ID仅用于抓包核对，合法报告身份仍为FlowTag/Session',
            order='原冻结顺序不变，每拓扑按48例分段重建部署及控制捕获'),
        source_sha256={n:sha(ROOT/n) for n in source_files()})


def validate(plan,current=False,output=None):
    expected=definition(plan['mode'])
    for key in expected.keys()-{'source_sha256'}:
        if plan[key]!=expected[key]:raise ValueError('冻结静态执行字段改变 '+key)
    if current and plan['source_sha256']!=expected['source_sha256']:raise ValueError('执行源码冻结后改变')
    if output is not None:
        for batch in plan['batches']:
            validate_paths(dict(networks={batch['network_id']:plan['networks'][batch['network_id']]},
                                execution_order=batch['entries']),output/batch['batch_id'])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('smoke','training'),required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();plan=definition(a.mode);validate(plan,True)
    a.output.mkdir(parents=True,exist_ok=False)
    (a.output/'stage-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    (a.output/'training-input.json').write_bytes((ROOT/INPUT).read_bytes())
    (a.output/'manifest.json').write_text(json.dumps(dict(status='prepared',execution_ready=True,
        business_generated=False,network_started=False,
        artifact_sha256={n:sha(a.output/n) for n in ('stage-plan.json','training-input.json')}),indent=2)+'\n')
    print(json.dumps(dict(status='prepared',mode=a.mode,cases=len(plan['execution_order']),batches=len(plan['batches']))))
