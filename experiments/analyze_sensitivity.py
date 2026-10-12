"""重建五条件敏感性的时效、费用及方法和条件配对。"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import shutil
from scalable_contract import ROOT,sha
from sensitivity_execution import validate
from sensitivity_pairs import summarize as paired_summary
from check_sensitivity_case import check_case
from check_observation_wire import load,rows,identity
from check_observation_matched import charges_for
from check_rolling_reference import same
from scale_static_bounds import output_frames,costs,generation_bounds
from analyze_network_static import check_cache_and_budget
from analyze_observer_comparison import controller_samples,age_bounds
from scalable_gate_check import check_final
from orchestration.network_spec import FixedNetwork
from scale_training_workload import inputs

SOURCES=('experiments/analyze_sensitivity.py','experiments/check_sensitivity_delivery.py',
    'experiments/sensitivity_execution.py','experiments/sensitivity_pairs.py','experiments/check_sensitivity_pairs.py',
    'experiments/plot_sensitivity.py','experiments/check_sensitivity_case.py','experiments/check_scale_comparison_case.py','experiments/check_scale_comparison_literature.py',
    'experiments/scale_static_tasks.py','experiments/scale_static_bounds.py','experiments/scale_training_contract.py','experiments/scale_training_workload.py',
    'experiments/scalable_contract.py','experiments/scalable_gate_check.py','experiments/scalable_observation_check.py',
    'experiments/scalable_adaptive_check.py','experiments/check_observation_wire.py','experiments/check_observation_matched.py',
    'experiments/check_rolling_reference.py','experiments/analyze_network_static.py','experiments/analyze_observer_comparison.py',
    'experiments/diagnose_observer_training.py','src/orchestration/network_spec.py','tests/test_sensitivity.py')


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def table(path,values):
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(values[0]),lineterminator='\n');w.writeheader();w.writerows(values)


def analyze(run,output):
    original=load(run/'manifest.json');plan=load(run/'stage-plan.json');validate(plan)
    same(original['status'] in ('passed','completed_with_failures'),True,'训练矩阵完整结束')
    same(original['completed_cases'],plan['expected_cases'],'全部计划案例分母')
    same(original['plan_sha256'],sha(run/'stage-plan.json'),'执行输入绑定')
    same(sha(run/'sensitivity-scope.json'),plan['sensitivity_scope']['sha256'],'方法及共同初始化范围绑定')
    same(load(run/'seed-audit.json'),plan['seed_audit'],'首例前新业务审计绑定')
    same(original['source_sha256'],plan['source_sha256'],'执行源码绑定')
    checks=0
    for name,digest in original['source_sha256'].items():same(sha(run/'executed-source'/name),digest,'实际执行源码快照');checks+=1
    for name,digest in original['artifact_sha256'].items():same(sha(run/name),digest,'实际运行全部产物');checks+=1
    same([b['batch_id'] for b in original['batches']],[b['batch_id'] for b in plan['batches']],'分批部署完整顺序')
    output.mkdir(parents=True,exist_ok=False)
    summary=[];verification=[];frames_total=0
    for batch,expected in zip(original['batches'],plan['batches']):
        b=run/batch['batch_id'];out=output/batch['batch_id'];out.mkdir()
        same([c['name'] for c in batch['cases']],[e['name'] for e in expected['entries']],'冻结分批案例顺序')
        rpc=rows(b/'rpc-operations.jsonl')
        full=[c for c in batch['cases'] if c['status']=='passed']
        frames=output_frames(b,batch['cases']) if len(full)==len(batch['cases']) else None
        for local,(case,entry) in enumerate(zip(batch['cases'],expected['entries'])):
            d=b/case['name'];target=out/case['name'];target.mkdir()
            for p in d.iterdir():
                if p.suffix in ('.json','.jsonl','.pcap'):shutil.copyfile(p,target/p.name)
            for k,v in entry.items():same(case[k],v,'冻结训练配置和种子');checks+=1
            spec=FixedNetwork(plan['networks'][entry['network_id']]);same(case['topology'],spec.record,'实际拓扑');same(case['block'],expected['block'],'实际部署块身份')
            for name,value in zip(('packets.json','notice-schedule.json','tasks.json'),inputs(spec,entry['scenario'],entry['business_seed'],local*1000)):
                same(load(d/name),value,'冻结业务和节点任务');checks+=1
            if case['status']!='passed' or frames is None:
                raise ValueError('存在工程失败，保留原始数据后需独立处理失败及同批抓包，不能静默丢弃 '+case['name'])
            checked=check_case(d,rpc);checked['cache_and_budget_checks']=check_cache_and_budget(d)
            checks+=checked['checks']+checked['observer_checks']+checked['cache_and_budget_checks']
            ps=load(d/'packets.json');reports=[r for r in rows(d/'collector.jsonl') if r.get('ether_type')==0x88b6]
            for f in load(d/'final-state.json'):
                stamps=[record['generated_us'] for r in reports if (r['session'],r['flow_tag'])==identity(f['flow']) for record in r['records'] if record['switch_id']==f['node']]
                checks+=check_final(f['has_sample'],f['last_timestamp'],stamps)
            generation=generation_bounds(d,case);available,samples=controller_samples(d,case,generation)
            visible=age_bounds(load(d/'tasks.json'),samples,case['origin_monotonic_ns'])
            cost=costs(d,case,frames[case['name']],charges_for(d,rpc))
            for name,value in [('generation-bounds.json',generation),('controller-samples.json',samples),('controller-visible-bounds.json',visible),('window-costs.json',cost)]:write(target/name,value)
            table(target/'output-frames.csv',frames[case['name']]);frames_total+=len(frames[case['name']]);checks+=len(frames[case['name']])
            verification.append(dict(batch_id=batch['batch_id'],case=case['name'],**checked))
            summary.append(dict(batch_id=batch['batch_id'],block=case['block'],case=case['name'],network_id=case['network_id'],scenario=case['scenario'],
                candidate_id=case['candidate_id'],condition_id=case['condition_id'],
                data_budget_multiplier=case['data_budget_multiplier'],observation_period_us=case['observation_period_us'],business_seed=case['business_seed'],position=case['position'],method=case['method'],
                sampling_nodes=case['topology']['sampling_nodes'],flows=len(case['topology']['flows']),configuration_positions=len(spec.keys),
                cost_layers=case['topology']['sampling_nodes']+3,packets=case['packet_count'],reports=case['report_count'],
                updates=case['successful_updates'],paid_counts=case['paid_counts'],status=case['status'],window_feasibility=cost['window_feasibility'],
                collector_loss=[generation['loss_lower'],generation['loss_upper']],controller_loss=[visible['loss_lower'],visible['loss_upper']],
                weighted_duration_us=visible['weighted_duration_us'],weighted_active_interval_coverage=generation['weighted_active_interval_coverage'],
                fee_rates={**{k:[None if cost['measurement_lower_bytes'] is None else cost['measurement_lower_bytes'][k]/8,
                    None if cost['measurement_upper_bytes'] is None else cost['measurement_upper_bytes'][k]/8]
                    for k in [*(f'e{n}' for n in range(1,case['topology']['sampling_nodes']+1)),'export']},
                    'api':[cost['api_bytes_per_s']]*2,'notice':[cost['local_notice_bytes_per_s']]*2},observed_nodes=sorted({r['node'] for r in samples}),
                observer_max_delay_ms=max((r['receipt_to_entry_upper_ms'] for r in available),default=None),
                export_bytes_per_s=None if cost['measurement_upper_bytes'] is None else cost['measurement_upper_bytes']['export']/8,
                api_bytes_per_s=cost['api_bytes_per_s'],local_notice_bytes_per_s=cost['local_notice_bytes_per_s']))
        for name in ('manifest.json','rpc-operations.jsonl','rpc.jsonl','rpc.pcap'):shutil.copyfile(b/name,out/name)
        for p in b.glob('network-*/*/*_out.pcap'):
            t=out/p.relative_to(b);t.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,t)
        print(json.dumps(dict(batch=batch['batch_id'],completed=len(summary),total=plan['expected_cases'])),flush=True)
    for name in ('stage-plan.json','sensitivity-scope.json','seed-audit.json','compiled-program.json','unit-tests.txt'):shutil.copyfile(run/name,output/name)
    shutil.copytree(run/'executed-source',output/'executed-source')
    for name in SOURCES:
        t=output/'analysis-source'/name;t.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,t)
    write(output/'source-manifest.json',original);write(output/'summary.json',summary);table(output/'summary.csv',summary)
    write(output/'verification.json',dict(checks=checks,cases=verification))
    if plan['mode']=='comparison':
        paired=paired_summary(output,summary,plan)
        for name,value in paired.items():write(output/(name.replace('_','-')+'.json'),value)
    totals=dict(cases=len(summary),packets=sum(r['packets'] for r in summary),reports=sum(r['reports'] for r in summary),output_frames=frames_total,
        within=sum(r['window_feasibility']=='within' for r in summary),updates=sum(r['updates'] for r in summary),paid_counts=sum(r['paid_counts'] for r in summary),
        completed_observations=sum(v['completed_observations'] for v in verification),skipped_observations=sum(v['skipped_observations'] for v in verification),
        skipped_decisions=sum(v['skipped_epochs'] for v in verification),
        budget_states=dict(Counter(r['window_feasibility'] for r in summary)))
    record=dict(status='passed',mode=plan['mode'],summary=totals,checks=checks,
        source_manifest_sha256=sha(output/'source-manifest.json'),analysis_source_sha256={n:sha(ROOT/n) for n in SOURCES},
        artifact_sha256={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()})
    write(output/'manifest.json',record)
    return dict(status='passed',checks=checks,**totals)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(analyze(a.run,a.output),ensure_ascii=False))
