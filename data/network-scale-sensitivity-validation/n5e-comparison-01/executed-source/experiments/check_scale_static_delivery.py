"""从公开分批抓包重建费用、年龄及有限静态双业务选择。"""
import argparse
import csv
import json
from pathlib import Path
from scalable_contract import sha
from scale_static_execution import validate
from check_scale_static_case import check_case
from check_observation_wire import load,rows
from check_observation_matched import charges_for
from check_rolling_reference import same
from scale_static_bounds import output_frames,costs,generation_bounds
from analyze_observer_comparison import controller_samples,age_bounds


def check_selection(summary,contract,selected,scores):
    checks=0;expected_choices=[];expected_scores=[]
    for name,pool in contract['candidates'].items():
        for scene in ('stable','shared_priority'):
            eligible=[]
            for candidate in pool:
                group=[r for r in summary if r['network_id']==name and r['scenario']==scene and r['candidate_id']==candidate['candidate_id']]
                same(sorted(r['business_seed'] for r in group),[11,29],'两训练业务逐候选完整分母')
                ok=len(group)==2 and all(r['status']=='passed' and r['window_feasibility']=='within' for r in group)
                worst=None if any(r['controller_loss'] is None for r in group) else max(r['controller_loss'][1] for r in group)
                mean=None if worst is None else sum(r['controller_loss'][1] for r in group)/2
                exported=None if any(r['export_bytes_per_s'] is None for r in group) else sum(r['export_bytes_per_s'] for r in group)/2
                row=dict(network_id=name,scenario=scene,candidate_id=candidate['candidate_id'],family=candidate['family'],config=candidate['config'],
                    eligible=ok,feasibility=[r['window_feasibility'] for r in group],worst_controller_upper=worst,mean_controller_upper=mean,mean_export_bytes_per_s=exported)
                expected_scores.append(row)
                if ok:eligible.append(row)
                checks+=1
            eligible.sort(key=lambda r:(r['worst_controller_upper'],r['mean_controller_upper'],r['mean_export_bytes_per_s'],tuple(-1 if v is None else v for v in r['config'])))
            expected_choices.append(dict(network_id=name,scenario=scene,eligible_candidates=len(eligible),
                selected=[dict(r,rank=i+1) for i,r in enumerate(eligible[:2])],status='selected_for_confirmation' if eligible else 'no_common_feasible_candidate'))
    same(scores,expected_scores,'独立重建全部有限评分及失败分母')
    same(selected,expected_choices,'独立预算资格、排序及首二候选')
    return checks+2


def check(output):
    m=load(output/'manifest.json');original=load(output/'source-manifest.json');plan=load(output/'stage-plan.json');validate(plan)
    same(m['status'],'passed','训练公开交付状态');same(sha(output/'source-manifest.json'),m['source_manifest_sha256'],'原执行绑定')
    checks=2
    for name,digest in m['artifact_sha256'].items():same(sha(output/name),digest,'全部公开产物摘要');checks+=1
    for name,digest in original['source_sha256'].items():same(sha(output/'executed-source'/name),digest,'实际执行源码');checks+=1
    summary=load(output/'summary.json');verification=load(output/'verification.json')['cases'];total_frames=0
    same([r['case'] for r in summary],[e['name'] for e in plan['execution_order']],'完整冻结案例及原顺序')
    for batch in original['batches']:
        b=output/batch['batch_id'];rpc=rows(b/'rpc-operations.jsonl');frames=output_frames(b,batch['cases'])
        for c in batch['cases']:
            d=b/c['name'];s=next(r for r in summary if r['case']==c['name'])
            actual=check_case(d,rpc);checks+=actual['checks']+actual['observer_checks']
            with (d/'output-frames.csv').open() as f:saved=list(csv.DictReader(f))
            same(saved,[{k:str(v) for k,v in row.items()} for row in frames[c['name']]],'逐帧费用来源');checks+=len(saved);total_frames+=len(saved)
            cost=costs(d,c,frames[c['name']],charges_for(d,rpc));age=generation_bounds(d,c)
            _,samples=controller_samples(d,c,age);visible=age_bounds(load(d/'tasks.json'),samples,c['origin_monotonic_ns'])
            for name,value in [('window-costs.json',cost),('generation-bounds.json',age),('controller-samples.json',samples),('controller-visible-bounds.json',visible)]:
                same(load(d/name),value,'实际费用和两位置年龄独立重建');checks+=1
            same(s['collector_loss'],[age['loss_lower'],age['loss_upper']],'采集端汇总')
            same(s['controller_loss'],[visible['loss_lower'],visible['loss_upper']],'控制器汇总')
            same(s['weighted_duration_us'],visible['weighted_duration_us'],'全部共享任务分母')
            same(s['cost_layers'],c['topology']['sampling_nodes']+3,'逐层费用分母')
            same(s['window_feasibility'],cost['window_feasibility'],'实际平均预算状态')
            same(s['export_bytes_per_s'],None if cost['measurement_upper_bytes'] is None else cost['measurement_upper_bytes']['export']/8,'条件导出费用上界')
            checks+=6
    if plan['mode']=='training':checks+=check_selection(summary,load(output/'training-input.json'),load(output/'selected-candidates.json'),load(output/'candidate-scores.json'))
    totals=dict(cases=len(summary),packets=sum(r['packets'] for r in summary),reports=sum(r['reports'] for r in summary),output_frames=total_frames,
        within=sum(r['window_feasibility']=='within' for r in summary),updates=sum(r['updates'] for r in summary),paid_counts=sum(r['paid_counts'] for r in summary),
        completed_observations=sum(r['completed_observations'] for r in verification),skipped_observations=sum(r['skipped_observations'] for r in verification),
        skipped_decisions=sum(r['skipped_epochs'] for r in verification),selected_cells=10 if plan['mode']=='training' else 0)
    same(m['summary'],totals,'全部实际网络训练汇总')
    return dict(status='passed',checks=checks,**totals)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path)
    a=p.parse_args();result=check(a.analysis)
    if a.output:a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
