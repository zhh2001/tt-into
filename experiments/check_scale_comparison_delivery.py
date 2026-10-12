"""从公开抓包独立重建规模费用、共享年龄、合法来源及完整配对。"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from scalable_contract import sha
from scale_comparison_execution import validate
from check_scale_comparison_case import check_case
from check_scale_comparison_pairs import check as check_pairs
from check_observation_wire import load,rows
from check_observation_matched import charges_for
from check_rolling_reference import same
from scale_static_bounds import output_frames,costs,generation_bounds
from analyze_observer_comparison import controller_samples,age_bounds


def check(output):
    m=load(output/'manifest.json');original=load(output/'source-manifest.json');plan=load(output/'stage-plan.json');validate(plan)
    same(m['status'],'passed','训练公开交付状态');same(sha(output/'source-manifest.json'),m['source_manifest_sha256'],'原执行绑定')
    checks=2
    for name,digest in m['artifact_sha256'].items():same(sha(output/name),digest,'全部公开产物摘要');checks+=1
    for name,digest in original['source_sha256'].items():same(sha(output/'executed-source'/name),digest,'实际执行源码');checks+=1
    summary=load(output/'summary.json');verification=load(output/'verification.json')['cases'];total_frames=0
    same([(r['batch_id'],r['case']) for r in summary],[(b['batch_id'],e['name']) for b in plan['batches'] for e in b['entries']],'完整分块身份及冻结顺序')
    for batch in original['batches']:
        b=output/batch['batch_id'];rpc=rows(b/'rpc-operations.jsonl');frames=output_frames(b,batch['cases'])
        for c in batch['cases']:
            d=b/c['name'];s=next(r for r in summary if (r['batch_id'],r['case'])==(batch['batch_id'],c['name']))
            same(s['block'],batch['block'],'实际部署块');checks+=1
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
            same(s['weighted_active_interval_coverage'],age['weighted_active_interval_coverage'],'共享节点权重覆盖')
            expected_rates={**{k:[None if cost['measurement_lower_bytes'] is None else cost['measurement_lower_bytes'][k]/8,
                None if cost['measurement_upper_bytes'] is None else cost['measurement_upper_bytes'][k]/8]
                for k in [*(f'e{n}' for n in range(1,c['topology']['sampling_nodes']+1)),'export']},
                'api':[cost['api_bytes_per_s']]*2,'notice':[cost['local_notice_bytes_per_s']]*2}
            same(s['fee_rates'],expected_rates,'全部实际费用层及空值')
            for k in ('network_id','scenario','candidate_id','business_seed','position','method'):
                same(s[k],c[k],'汇总与实际冻结案例身份')
            checks+=14
    if plan['mode']=='comparison':
        checks+=check_pairs(output)
    totals=dict(cases=len(summary),packets=sum(r['packets'] for r in summary),reports=sum(r['reports'] for r in summary),output_frames=total_frames,
        within=sum(r['window_feasibility']=='within' for r in summary),updates=sum(r['updates'] for r in summary),paid_counts=sum(r['paid_counts'] for r in summary),
        completed_observations=sum(r['completed_observations'] for r in verification),skipped_observations=sum(r['skipped_observations'] for r in verification),
        skipped_decisions=sum(r['skipped_epochs'] for r in verification),
        budget_states=dict(Counter(r['window_feasibility'] for r in summary)))
    same(m['summary'],totals,'全部实际网络训练汇总')
    return dict(status='passed',checks=checks,**totals)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path)
    a=p.parse_args();result=check(a.analysis)
    if a.output:a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
