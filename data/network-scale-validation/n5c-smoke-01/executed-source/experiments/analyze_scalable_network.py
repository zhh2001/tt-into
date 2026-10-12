"""N5a完整输入、实际链路、合法观察和共享任务的独立验收。"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import shutil

from scalable_contract import validate, sha, ROOT
from check_scalable_network import check_case
from check_observation_wire import load, rows, identity
from check_observation_matched import charges_for
from check_rolling_reference import same
from scalable_bounds import output_frames, costs, generation_bounds
from analyze_network_static import check_cache_and_budget
from analyze_observer_comparison import controller_samples, age_bounds
from orchestration.network_spec import FixedNetwork
from scalable_workload import inputs
from scalable_gate_check import check_final

ANALYSIS_SOURCES=('experiments/analyze_scalable_network.py','experiments/check_scalable_delivery.py',
    'experiments/scalable_bounds.py','experiments/scalable_gate_check.py','experiments/check_scalable_network.py',
    'experiments/scalable_observation_check.py','experiments/scalable_adaptive_check.py',
    'experiments/analyze_observer_comparison.py','experiments/analyze_network_static.py',
    'experiments/check_observation_matched.py','experiments/check_observation_wire.py','experiments/check_rolling_reference.py',
    'experiments/scalable_contract.py','experiments/scalable_workload.py','src/orchestration/network_spec.py',
    'tests/test_scalable_network.py')


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def table(path, values):
    with path.open('w', newline='') as f:
        writer=csv.DictWriter(f, fieldnames=list(values[0]), lineterminator='\n'); writer.writeheader(); writer.writerows(values)


def audit(run):
    m, plan = load(run / 'manifest.json'), load(run / 'stage-plan.json')
    validate(plan)
    same(m['status'], 'passed', '完整实际执行')
    same(len(m['cases']), 8, '八案例分母')
    same(m['plan_sha256'], sha(run / 'stage-plan.json'), '执行前准备摘要')
    same(m['source_sha256'], plan['source_sha256'], '准备与执行源码一致')
    same(m['business_seeds'], [], '正确性输入不消费新测试种子')
    same([(d['network_id'],d['status']) for d in m['deployments']], [(n,'passed') for n in plan['networks']], '完整启动及清理两网络')
    same(sha(run / 'compiled-program.json'),m['p4_json_sha256'],'实际编译数据面')
    checks=0
    for n,digest in m['source_sha256'].items(): same(sha(run / 'executed-source' / n),digest,'执行源码快照'); checks+=1
    for n,digest in m['artifact_sha256'].items(): same(sha(run / n),digest,'全部原始产物'); checks+=1
    rpc=rows(run / 'rpc-operations.jsonl'); results=[]
    for i,(c,e) in enumerate(zip(m['cases'],plan['execution_order'])):
        directory=run / c['name']; spec=FixedNetwork(plan['networks'][e['network_id']])
        for k,v in e.items(): same(c[k],v,'冻结方法与输入');checks+=1
        same(c['topology'],spec.record,'实际拓扑与冻结定义')
        expected=inputs(spec,e['method'],i*1000)
        for name,value in zip(('packets.json','notice-schedule.json','tasks.json'),expected):
            same(load(directory/name),value,'冻结确定性业务');checks+=1
        result=check_case(directory,rpc); result['cache_and_budget_checks']=check_cache_and_budget(directory)
        ps=load(directory/'packets.json')
        send={r['packet_id']:r for r in rows(directory/'send.jsonl')}
        dest={r['packet_id']:r for r in rows(directory/'destination.jsonl') if r.get('kind')=='frame' and r.get('src')=='10.0.0.1'}
        for read in rows(directory/'count-observations.jsonl'):
            r=rpc[read['rpc_index']]; descriptor=next(f for f in spec.record['flows'] if identity(f['identity'])==identity(read['observation']['flow']))
            ordinal=spec.record['flows'].index(descriptor)+1
            relevant=[p for p in ps if p['flow']==ordinal and p['registered']]
            lower=sum(dest[p['packet_id']]['receive_monotonic_ns']<r['before_monotonic_ns'] for p in relevant)
            upper=sum(send[p['packet_id']]['send_before_monotonic_ns']<=r['after_monotonic_ns'] for p in relevant)
            if not lower<=r['result']<=upper: raise ValueError('付费源计数不在真实发送/完成因果界内')
            checks+=1
        actual=[r for r in rows(directory/'collector.jsonl') if r.get('ether_type')==0x88b6 and not next(p for p in ps if p['packet_id']==r['packet_id'])['probe']]
        for f in load(directory/'final-state.json'):
            stamps=[x['generated_us'] for r in actual if (r['session'],r['flow_tag'])==identity(f['flow']) for x in r['records'] if x['switch_id']==f['node']]
            checks+=check_final(f['has_sample'],f['last_timestamp'],stamps)
        results.append(dict(name=c['name'],**result))
    return dict(status='passed',input_checks=checks,cases=results,
        **{k:sum(c[k] for c in results) for k in ('packet_count','report_count','decision_count','successful_updates','paid_counts','checks','observer_checks','cache_and_budget_checks','visible_reports','shared_cache_advances','skipped_epochs','completed_observations','skipped_observations')})


def analyze(run, output):
    verification=audit(run); m=load(run/'manifest.json'); rpc=rows(run/'rpc-operations.jsonl')
    frames=output_frames(run,m['cases'])
    output.mkdir(parents=True,exist_ok=False)
    summary=[]; frame_checks=0
    for c in m['cases']:
        directory,target=run/c['name'],output/c['name'];target.mkdir()
        for p in directory.iterdir():
            if p.suffix in ('.json','.jsonl','.pcap'): shutil.copyfile(p,target/p.name)
        ps=load(directory/'packets.json'); actions=rows(directory/'actions.jsonl')
        sends={r['packet_id']:r for r in rows(directory/'send.jsonl')}
        dest={r['packet_id']:r for r in rows(directory/'destination.jsonl') if r.get('kind')=='frame' and r.get('src')=='10.0.0.1'}
        ordinary={p['packet_id']:p for p in ps if not p['probe']}
        reports=[r for r in rows(directory/'collector.jsonl') if r.get('ether_type')==0x88b6]
        for r in reports:
            p=ordinary.get(r['packet_id'])
            if p is None: continue
            node=r['records'][0]['switch_id']; flow=(r['session'],r['flow_tag'])
            matching=[a for a in actions if a['success'] and a['node']==node and identity(a['flow'])==flow and a['field']=='enabled']
            lo=sends[p['packet_id']]['send_before_monotonic_ns'];hi=dest[p['packet_id']]['receive_monotonic_ns']
            completed=[a for a in matching if rpc[a['rpc_index']]['after_monotonic_ns']<lo]
            possible=[completed[-1]['value']] if completed else []
            possible += [a['value'] for a in matching if rpc[a['rpc_index']]['before_monotonic_ns']<=hi and rpc[a['rpc_index']]['after_monotonic_ns']>=lo]
            if True not in possible: raise ValueError('真实采样不符合实际许可生效因果界')
            frame_checks+=1
        generation=generation_bounds(directory,c)
        available,samples=controller_samples(directory,c,generation)
        visible=age_bounds(load(directory/'tasks.json'),samples,c['origin_monotonic_ns'])
        cost=costs(directory,c,frames[c['name']],charges_for(directory,rpc))
        for n,v in [('generation-bounds.json',generation),('controller-samples.json',samples),('controller-visible-bounds.json',visible),('window-costs.json',cost)]:write(target/n,v)
        table(target/'output-frames.csv',frames[c['name']])
        summary.append(dict(case=c['name'],network_id=c['network_id'],method=c['method'],sampling_nodes=c['topology']['sampling_nodes'],
            flows=len(c['topology']['flows']),configuration_positions=len(FixedNetwork(c['topology']).keys),
            cost_layers=c['topology']['sampling_nodes']+3,packets=c['packet_count'],reports=c['report_count'],updates=c['successful_updates'],
            paid_counts=c['paid_counts'],status=c['status'],window_feasibility=cost['window_feasibility'],
            collector_loss=[generation['loss_lower'],generation['loss_upper']],controller_loss=[visible['loss_lower'],visible['loss_upper']],
            weighted_duration_us=visible['weighted_duration_us'],observed_nodes=sorted({r['node'] for r in samples}),
            observer_max_delay_ms=max((r['receipt_to_entry_upper_ms'] for r in available),default=None),
            api_bytes_per_s=cost['api_bytes_per_s'],local_notice_bytes_per_s=cost['local_notice_bytes_per_s']))
        frame_checks+=len(frames[c['name']])
    for name in ('rpc-operations.jsonl','rpc.jsonl','rpc.pcap','stage-plan.json','compiled-program.json','unit-tests.txt'):
        shutil.copyfile(run/name,output/name)
    for p in run.glob('network-*/*/*_out.pcap'):
        target=output/p.relative_to(run);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    shutil.copytree(run/'executed-source',output/'executed-source')
    for name in ANALYSIS_SOURCES:
        target=output/'analysis-source'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    write(output/'source-manifest.json',m);write(output/'verification.json',dict(verification,output_frame_checks=frame_checks))
    write(output/'summary.json',summary);table(output/'summary.csv',summary)
    summary_totals=dict(cases=len(summary),packets=sum(r['packets'] for r in summary),reports=sum(r['reports'] for r in summary),
        output_frames=sum(len(r) for r in frames.values()),updates=sum(r['updates'] for r in summary),paid_counts=sum(r['paid_counts'] for r in summary),
        within=sum(r['window_feasibility']=='within' for r in summary),cost_layers_by_network={r['network_id']:r['cost_layers'] for r in summary})
    manifest=dict(status='passed',run_id=run.name,scope='确定性通用入口正确性，非性能排名',summary=summary_totals,
        source_manifest_sha256=sha(output/'source-manifest.json'),
        analysis_source_sha256={name:sha(ROOT/name) for name in ANALYSIS_SOURCES},
        artifact_sha256={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()})
    write(output/'manifest.json',manifest)
    return dict(status='passed',**summary_totals,checks=verification['input_checks']+verification['checks']+verification['observer_checks']+verification['cache_and_budget_checks']+frame_checks)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(analyze(a.run,a.output),ensure_ascii=False))
