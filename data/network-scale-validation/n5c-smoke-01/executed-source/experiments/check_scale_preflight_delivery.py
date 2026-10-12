"""从公开抓包、调用和观察事件重建N5b交付结果。"""
import argparse
import csv
import json
from pathlib import Path
from scale_preflight_contract import validate, sha
from check_scale_preflight import check_case
from check_observation_wire import load, rows
from check_observation_matched import charges_for
from check_rolling_reference import same
from scalable_bounds import output_frames, costs, generation_bounds
from analyze_observer_comparison import controller_samples, age_bounds


def check(output):
    m, original=load(output/'manifest.json'),load(output/'source-manifest.json')
    validate(load(output/'stage-plan.json'))
    same(m['status'],'passed','完整公开交付')
    same(sha(output/'source-manifest.json'),m['source_manifest_sha256'],'原运行记录绑定')
    checks=2
    for name,digest in m['artifact_sha256'].items():same(sha(output/name),digest,'交付产物摘要');checks+=1
    for name,digest in original['source_sha256'].items():same(sha(output/'executed-source'/name),digest,'实际执行源码');checks+=1
    frames=output_frames(output,original['cases']);rpc=rows(output/'rpc-operations.jsonl')
    summary=load(output/'summary.json')
    same([r['case'] for r in summary],[c['name'] for c in original['cases']],'六案例完整交付')
    for c,s in zip(original['cases'],summary):
        directory=output/c['name'];actual=check_case(directory,rpc)
        checks+=actual['checks']+actual['observer_checks']
        with (directory/'output-frames.csv').open() as f: saved=list(csv.DictReader(f))
        same(saved,[{k:str(v) for k,v in r.items()} for r in frames[c['name']]],'逐帧输出来源')
        cost=costs(directory,c,frames[c['name']],charges_for(directory,rpc))
        age=generation_bounds(directory,c)
        _,samples=controller_samples(directory,c,age)
        visible=age_bounds(load(directory/'tasks.json'),samples,c['origin_monotonic_ns'])
        for name,value in [('window-costs.json',cost),('generation-bounds.json',age),('controller-samples.json',samples),('controller-visible-bounds.json',visible)]:
            same(load(directory/name),value,'费用和两位置年龄由公开事件重建');checks+=1
        same(s['collector_loss'],[age['loss_lower'],age['loss_upper']],'采集端年龄汇总')
        same(s['controller_loss'],[visible['loss_lower'],visible['loss_upper']],'控制器年龄汇总')
        same(s['weighted_duration_us'],visible['weighted_duration_us'],'未知任务完整分母')
        same(s['cost_layers'],c['topology']['sampling_nodes']+3,'实际全部费用层')
        same(s['window_feasibility'],cost['window_feasibility'],'平均窗口状态')
        checks+=6+len(saved)
    expected=dict(cases=6,packets=sum(r['packets'] for r in summary),reports=sum(r['reports'] for r in summary),
        output_frames=sum(len(r) for r in frames.values()),updates=sum(r['updates'] for r in summary),paid_counts=sum(r['paid_counts'] for r in summary),
        within=sum(r['window_feasibility']=='within' for r in summary),cost_layers_by_network={r['network_id']:r['cost_layers'] for r in summary})
    same(m['summary'],expected,'完整正确性汇总')
    return dict(status='passed',checks=checks,**expected)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--analysis',type=Path,required=True)
    print(json.dumps(check(p.parse_args().analysis),ensure_ascii=False))
