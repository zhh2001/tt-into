"""只确认本轮模型参照与网络选择，不新增配置网格搜索。"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import shutil

ORDER_SEEDS = (17320508,26457513,31622777)


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def prepare(analysis,diagnostic,output):
    m=json.loads((analysis/'manifest.json').read_text())
    d=json.loads((diagnostic/'manifest.json').read_text())
    if m['status'] != 'passed' or m['smoke'] or len(m['cases']) != 192 or d['status'] != 'passed' \
            or d['analysis_manifest_sha256'] != sha(analysis/'manifest.json'):
        raise ValueError('确认输入未通过或诊断来源不同')
    names=('network-selections.json','candidate-plan.json','summary-checks.json')
    for n in names:
        if sha(analysis/n) != m['artifact_sha256'][n]: raise ValueError('确认输入摘要不同')
    selected=json.loads((analysis/'network-selections.json').read_text())
    original=json.loads((analysis/'candidate-plan.json').read_text())
    cells=[]
    for s in selected:
        if s['status'] != 'selected' or not s['model_feasible']: raise ValueError('参照或选择缺少共同可行性')
        group=next(g for g in original['groups'] if (g['scenario'],g['budget_level'])==(s['scenario'],s['budget_level']))
        for index in sorted({s['selected_candidate'],s['model_candidate']}):
            candidate=group['candidates'][index]
            roles=[]
            if index==s['model_candidate']: roles.append('frozen_model_reference')
            if index==s['selected_candidate']: roles.append('network_selected')
            cells.append({'scenario':s['scenario'],'budget_level':s['budget_level'],
                          'candidate_index':index,'config':candidate['config'],'roles':roles})
    order=[]
    for block,seed in enumerate(ORDER_SEEDS,1):
        batch=[dict(c,seed=business) for c in cells for business in (11,29)]
        random.Random(seed).shuffle(batch)
        order += [dict(c,block=block,order_seed=seed,
                       name=f"b{block:02d}-{c['scenario']}-{c['budget_level']}-s{c['seed']}-c{c['candidate_index']:02d}") for c in batch]
    if len(cells)!=6 or len(order)!=36 or len({r['name'] for r in order})!=36:
        raise ValueError('确认配置或声明分母错误')
    output.mkdir(parents=True,exist_ok=False)
    source=Path(__file__).resolve()
    target=output/'executed-source/experiments'/source.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(source,target)
    plan={'status':'prepared','scope':'事后有限重复确认，未执行，不是新业务盲测或新增网格搜索',
          'analysis_manifest_sha256':sha(analysis/'manifest.json'),'diagnostic_manifest_sha256':sha(diagnostic/'manifest.json'),
          'input_sha256':{n:sha(analysis/n) for n in names},'source_sha256':{'experiments/'+source.name:sha(source)},
          'business_seeds':[11,29],'blocks':3,'order_seeds':list(ORDER_SEEDS),'expected_cases':36,
          'unique_config_cells':cells,'execution_order':order,
          'selection_rule':'候选全部六个完整案例费用上界达标且年龄可评价，再按最坏损失上界、平均上界、启用项数、配置编码排序',
          'evaluation':'两业务每候选三个部署重复，均为已消费训练；采集端主选择、控制器视图诊断，保留所有失败与区间',
          'stopping_rule':'不凭微小重叠差距扩展网格；完成后冻结静态对照并准备动态校准，工程错误及来源破坏另处理'}
    (output/'confirmation-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'status':'prepared','unique_config_cells':len(cells),'expected_cases':36},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--analysis',type=Path,required=True)
    p.add_argument('--diagnostic',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    prepare(a.analysis,a.diagnostic,a.output)
