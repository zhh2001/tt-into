"""从公开分块抓包独立重建静态确认与共同初始化。"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from scalable_contract import sha
from scale_confirmation_execution import validate
from check_scale_static_case import check_case
from check_observation_wire import load,rows
from check_observation_matched import charges_for
from check_rolling_reference import same
from scale_static_bounds import output_frames,costs,generation_bounds
from analyze_observer_comparison import controller_samples,age_bounds


def check_selection(summary,shortlist,frozen,scores,plan):
    rebuilt=[];cells=[];checks=0
    for cell in shortlist:
        candidates=[]
        for candidate in cell['selected']:
            group=[r for r in summary if (r['network_id'],r['scenario'],r['candidate_id'])==
                   (cell['network_id'],cell['scenario'],candidate['candidate_id'])]
            same(sorted((r['block'],r['business_seed']) for r in group),
                 [(b,s) for b in (0,1,2) for s in (11,29)],'独立六例完整资格')
            group.sort(key=lambda r:(r['block'],r['business_seed']))
            age_ok=all(r['controller_loss'] is not None for r in group)
            eligible=all(r['status']=='passed' and r['window_feasibility']=='within' for r in group)
            row=dict(network_id=cell['network_id'],scenario=cell['scenario'],candidate_id=candidate['candidate_id'],
                config=candidate['config'],training_rank=candidate['rank'],eligible=eligible,
                feasibility=[r['window_feasibility'] for r in group],
                worst_controller_upper=None if not age_ok else max(r['controller_loss'][1] for r in group),
                mean_controller_upper=None if not age_ok else sum(r['controller_loss'][1] for r in group)/6,
                mean_controller_loss=None if not age_ok else [sum(r['controller_loss'][i] for r in group)/6 for i in (0,1)],
                mean_collector_loss=None if any(r['collector_loss'] is None for r in group) else
                    [sum(r['collector_loss'][i] for r in group)/6 for i in (0,1)],
                mean_export_bytes_per_s=None if any(r['export_bytes_per_s'] is None for r in group) else
                    sum(r['export_bytes_per_s'] for r in group)/6)
            rebuilt.append(row);checks+=1
            if eligible:candidates.append(row)
        candidates.sort(key=lambda r:(r['worst_controller_upper'],r['mean_controller_upper'],
            r['mean_export_bytes_per_s'],tuple(-1 if v is None else v for v in r['config'])))
        cells.append(dict(network_id=cell['network_id'],scenario=cell['scenario'],eligible_candidates=len(candidates),
            selected=candidates[0] if candidates else None,
            status='frozen_common_initialization' if candidates else 'no_common_feasible_candidate'))
    same(scores,rebuilt,'独立重建二十候选的全部评分和费用失败')
    expected=dict(status='frozen' if all(c['selected'] is not None for c in cells) else 'frozen_with_unavailable_cells',
        confirmation_input_sha256=plan['confirmation_input']['sha256'],selection_rule=plan['confirmation_rule'],
        methods=['static','feedback','phase_joint','phase_guard','intopp_due','d_into_tt'],cells=cells)
    same(frozen,expected,'独立排序及共同初始化完整冻结')
    return checks+2


def check_repeats(output,summary):
    saved=load(output/'seed-repeat-diagnostics.json');checks=0
    same(sorted((g['network_id'],g['scenario'],g['candidate_id'],g['business_seed']) for g in saved),
         sorted({(c['network_id'],c['scenario'],c['candidate_id'],c['business_seed']) for c in summary}),'完整且不重复的四十重复组')
    for group in saved:
        cases=sorted([c for c in summary if (c['network_id'],c['scenario'],c['candidate_id'],c['business_seed'])==
            (group['network_id'],group['scenario'],group['candidate_id'],group['business_seed'])],key=lambda c:c['block'])
        same([c['block'] for c in cases],[0,1,2],'独立部署重复完整性')
        same(group['cases'],[c['batch_id']+'/'+c['case'] for c in cases],'独立分块案例身份')
        positions=[]
        for case in cases:
            d=output/case['batch_id']/case['case'];packets={p['packet_id']:p for p in load(d/'packets.json')}
            positions.append(sorted((packets[r['packet_id']]['flow'],packets[r['packet_id']]['offset_ns'],record['switch_id'])
                for r in rows(d/'collector.jsonl') if r.get('ether_type')==0x88b6 for record in r['records']))
        same(group['same_sample_positions'],positions[0]==positions[1]==positions[2],'独立实际采样位置')
        same(group['sample_counts'],[len(p) for p in positions],'独立采样次数')
        same(group['feasibility'],[c['window_feasibility'] for c in cases],'独立重复预算')
        for place in ('collector','controller'):
            bounds=[c[place+'_loss'] for c in cases];mid=[sum(r)/2 for r in bounds]
            same(group[place+'_bounds'],bounds,'独立重复年龄区间')
            same(group[place+'_common_overlap'],max(r[0] for r in bounds)<=min(r[1] for r in bounds),'独立条件交集')
            same(group[place+'_midpoint_range'],max(mid)-min(mid),'独立部署变化')
        checks+=11
    same(len(saved),40,'完整四十配置与业务重复组')
    expected=dict(groups=40,same_positions_groups=sum(g['same_sample_positions'] for g in saved),
        collector_overlap_groups=sum(g['collector_common_overlap'] for g in saved),
        controller_overlap_groups=sum(g['controller_common_overlap'] for g in saved),
        max_controller_midpoint_range=max(g['controller_midpoint_range'] for g in saved))
    same(load(output/'repeat-summary.json'),expected,'完整部署重复汇总')
    return checks+2


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
            checks+=6
    frozen_cells=0
    if plan['mode']=='confirmation':
        checks+=check_selection(summary,load(output/'confirmation-selected.json'),load(output/'frozen-static.json'),load(output/'confirmation-scores.json'),plan)
        checks+=check_repeats(output,summary)
        frozen_cells=sum(c['selected'] is not None for c in load(output/'frozen-static.json')['cells'])
    totals=dict(cases=len(summary),packets=sum(r['packets'] for r in summary),reports=sum(r['reports'] for r in summary),output_frames=total_frames,
        within=sum(r['window_feasibility']=='within' for r in summary),updates=sum(r['updates'] for r in summary),paid_counts=sum(r['paid_counts'] for r in summary),
        completed_observations=sum(r['completed_observations'] for r in verification),skipped_observations=sum(r['skipped_observations'] for r in verification),
        skipped_decisions=sum(r['skipped_epochs'] for r in verification),selected_cells=10 if plan['mode']=='confirmation' else 0,frozen_cells=frozen_cells,
        budget_states=dict(Counter(r['window_feasibility'] for r in summary)))
    same(m['summary'],totals,'全部实际网络训练汇总')
    return dict(status='passed',checks=checks,**totals)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path)
    a=p.parse_args();result=check(a.analysis)
    if a.output:a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
