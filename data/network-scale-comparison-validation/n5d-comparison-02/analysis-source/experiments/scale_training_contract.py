"""有限静态规模训练的候选、业务、预算和执行顺序冻结。"""
import argparse
import copy
import json
import random
from pathlib import Path
from scalable_contract import ROOT,sha,source_files
from orchestration.network_spec import fork_chain,FixedNetwork
from scale_preflight_contract import method_parameters,REFERENCE

TOPOLOGIES=((3,2),(5,2),(8,2),(5,4),(5,8))
SCENARIOS=('stable','shared_priority')
TRAIN_SEEDS=(11,29)
GRID=(50000,100000,150000,200000,400000,600000,800000,1000000)
LIMIT=48
ORDER_SEED=51802


def budget_for(spec):
    flows=len(spec.record['flows'])
    if flows%2:raise ValueError('共同预算只冻结偶数流的规模轴')
    return dict(link_bytes_per_s={f'e{n}':(240 if n in (1,2) else 390)*flows//2 for n in range(1,spec.record['sampling_nodes']+1)},
                export_bytes_per_s=900*flows//2,control_bytes_per_s=1200)


def foundation(spec):
    rates={v:0 for v in spec.links.values()}
    for route in spec.routes.values():
        for obj in route:rates[spec.links[obj]]+=8*12
    return dict(per_flow_rate_pps=8,base_header_bytes=12,unavoidable_link_bytes_per_s=rates,
                disabled_still_carries_base_header=True)


def candidates(spec,profiles):
    keys=spec.keys
    rows=[];seen=set()
    def add(config,family,details):
        config=tuple(config)
        if config in seen:return
        seen.add(config)
        rows.append(dict(candidate_id=f'C{len(rows):02d}',config=list(config),family=family,details=details))
    for theta in GRID:add([theta]*len(keys),'uniform',dict(threshold_us=theta))
    add([None]*len(keys),'disabled',{})
    for shift in range(8):
        chosen={n:[i for i,(node,_) in enumerate(keys) if node==n][shift%sum(node==n for node,_ in keys)] for n in range(1,spec.record['sampling_nodes']+1)}
        for theta in (50000,400000):
            add([theta if i==chosen[n] else None for i,(n,_) in enumerate(keys)],'single_carrier',dict(rotation=shift,threshold_us=theta))
    ordinals={f:i for i,f in enumerate(sorted(spec.routes))}
    for rotation in range(8):
        add([GRID[(n+ordinals[f]+rotation)%8] for n,f in keys],'staggered',dict(rotation=rotation))
    for profile in profiles:
        cfg=profile['initial_config']
        add([cfg[0 if n==1 else 2] if n in (1,2) else cfg[1 if ordinals[f]%2==0 else 3] for n,f in keys],
            'prior_training_extension',dict(scenario=profile['scenario'],source_config=cfg,
                rule='入口1/2沿用两入口值，共享链按流奇偶沿用原共享节点值'))
    if len(rows)>LIMIT:raise ValueError('声明模板超过有限候选上限')
    rng=random.Random(982000+100*spec.record['sampling_nodes']+len(spec.record['flows']))
    attempt=0
    while len(rows)<LIMIT:
        attempt+=1
        config=[rng.choice(GRID) if rng.random()<.5 else None for _ in keys]
        add(config,'fixed_random_grid',dict(draw_index=attempt))
        if attempt>100000:raise ValueError('固定候选去重无法补齐')
    return rows


def definition():
    profiles=json.loads((ROOT/REFERENCE).read_text())['reference']['profiles']
    networks={f'n{n}f{f}':fork_chain(n,f).record for n,f in TOPOLOGIES}
    pools={name:candidates(FixedNetwork(record),profiles) for name,record in networks.items()}
    network_order=list(networks)
    random.Random(ORDER_SEED).shuffle(network_order)
    entries=[]
    params=method_parameters()['static']
    for name in network_order:
        spec=FixedNetwork(networks[name])
        local=[dict(name=f'{name}-{scenario}-{r["candidate_id"]}-s{seed}',network_id=name,scenario=scenario,
                    candidate_id=r['candidate_id'],business_seed=seed,method='static',initial_config=r['config'],
                    **copy.deepcopy(params),budget=budget_for(spec),scope='有限静态规模训练，无独立测试业务')
               for scenario in SCENARIOS for r in pools[name] for seed in TRAIN_SEEDS]
        random.Random(ORDER_SEED+100*spec.record['sampling_nodes']+len(spec.record['flows'])).shuffle(local)
        for entry in local:entry['position']=len(entries);entries.append(entry)
    return dict(stage='n5b_finite_static_training',status='prepared',execution_ready=False,
        pending='网络训练执行适配器与单区间任务检查须在首例前单独验收并冻结',
        networks=networks,network_order=network_order,candidates=pools,expected_cases=960,execution_order=entries,
        candidate_limit_per_topology=LIMIT,business_seeds=list(TRAIN_SEEDS),seed_role='已消费训练种子复用，不是新测试',
        independent_test=False,test_seeds=[],methods_later=['static','feedback','phase_joint','phase_guard','intopp_due','d_into_tt'],
        observation_period_us=100000,decision_period_us=1000000,capacity=1,
        duration_us=10000000,measurement_window_us=[2000000,10000000],local_notice_budget_bytes_per_s=128,
        workload=dict(scenarios=list(SCENARIOS),per_flow_rate_pps=8,phase_fraction_range=[.1,.9],
            segment_boundaries_us=[0,5000000,10000000],primary='控制器加权截断生成年龄',clip=4,
            tasks='每个采样节点一个2至10秒区间，末节点采用重点阈值和权重',
            flow_prefix='相同种子时扩流保持原流包时序，两个场景相同业务包时序'),
        budgets={name:budget_for(FixedNetwork(r)) for name,r in networks.items()},
        foundation={name:foundation(FixedNetwork(r)) for name,r in networks.items()},
        selection=dict(eligibility='两训练业务均工程通过且全部实际条件平均窗口费用within',
            order=['两业务控制器损失上界最大值','两业务控制器损失上界均值','两业务实际导出B/s均值','配置编码'],
            configuration_encoding='None=-1，其余按阈值整数词典序',
            failed_points='完整保留，不剔除后重新记为更小的训练分母',
            next_confirmation='训练后另冻结两个首选配置的相同训练业务部署重复，冻结共同初始化后再生成新测试'),
        candidate_design=dict(template_order=['uniform','disabled','single_carrier','staggered','prior_training_extension','fixed_random_grid'],
            duplicate_rule='相同完整配置保留首次来源，随机补齐至48',
            random_seed_formula='982000+100*sampling_nodes+flows',execution_order_seed=ORDER_SEED,
            scope='每拓扑48个有限候选，五节点两流只计算一次，不作全网格最优声明'),
        source_sha256={n:sha(ROOT/n) for n in source_files()},
        parameter_reference=dict(path=REFERENCE,sha256=sha(ROOT/REFERENCE)),
        budgets_scope='带内与导出随流数相对两流高档等比例，API1200和IPC128固定，绝对费用分列')


def validate(plan,current=False):
    expected=definition()
    for key in expected.keys()-{'source_sha256'}:
        if plan[key]!=expected[key]:raise ValueError('训练合同改变 '+key)
    if current and plan['source_sha256']!=expected['source_sha256']:raise ValueError('训练冻结后源码改变')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    out=p.parse_args().output;plan=definition();validate(plan,True)
    out.mkdir(parents=True,exist_ok=False)
    (out/'training-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps(dict(status='prepared',execution_ready=False,
        business_generated=False,network_started=False,artifact_sha256={'training-plan.json':sha(out/'training-plan.json')}),indent=2)+'\n')
    print(json.dumps(dict(status='prepared',topologies=5,candidates=240,cases=960,business_seeds=[11,29],execution_ready=False)))
