#!/usr/bin/env python3
"""将独立成本模型核对真实前缀链路字节，保存固定账本与小规模合成参照。"""
import argparse
from dataclasses import asdict,replace
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
import numpy
import scipy
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from orchestration.budget_model import Task,Packet,Budget,ControlEvent,FORMATS,costs,ledger_cost
from orchestration.budget_oracle import solve

sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
rows=lambda p:[json.loads(x) for x in p.read_text().splitlines() if x]


def check_run(run):
    summary=json.loads((run/'summary.json').read_text())
    fmt=FORMATS[summary['format_name']]
    checks=[]
    for c in summary['cases']:
        if c['configuration']['mode']!=0: continue  # 下界不模拟替换及其沿途费用。
        d=run/c['name']
        if not (d/'observations.jsonl').exists() and c['name'].startswith(('dev_','feedback')): continue
        originals=json.loads((d/'packets.json').read_text())
        wire=rows(d/'wire-observations.jsonl')
        first={w['packet_id']:w for w in wire if w['node']==1}
        reported={r['packet_id']:r for r in rows(d/'collector.jsonl') if r.get('records')}
        observations={r['packet_id']:r for r in rows(d/'observations.jsonl')} if (d/'observations.jsonl').exists() else {}
        packets=[]; selected={}; enabled={}
        for p in originals:
            pid=p['packet_id']; ob=observations.get(pid)
            # 时间占位只用于字节核对，不据此评价年龄或门控。
            selected[pid]=[i+1 for i,t in enumerate(ob['nodes']) if t['trace_decision']==1] if ob else [r['switch_id'] for r in reported.get(pid,{}).get('records',[])]
            emitted=not ob or not ob['sink_report_dropped']
            packets.append(Packet(pid,p['flow'],(1,2,3),(0,0,0),(0,0,0),0 if emitted else None,
                                  ip_bytes=p['ip_length'],capacity=max(c['configuration']['capacities']),export_emitted=emitted))
            enabled[pid]=first[pid]['ip_bytes']>p['ip_length']
        cost=costs(packets,selected,fmt,enabled)
        measured=c['measured_cost']
        assert cost['inband_per_link_bytes']==measured['inband_per_link_bytes']
        assert cost['export_bytes_without_padding']==measured['export_bytes']
        assert cost['export_bytes']==measured['export_bytes_with_minimum_ethernet_padding']
        checks.append({'case':c['name'],'packets':len(packets),'cost':cost,'passed':True,
                       'source_plan':'从首链路恢复实际封装，逐链路字节独立核对；不是采样决策预测或年龄复现'})
    api=rows(run/'control-events.jsonl')
    mapping={'installation':'installation','cold_start_reset':'installation','online_observation':'observation',
             'online_update':'update','loss_injection':'calibration','diagnostic_read':'validation'}
    origin=min(c['start_monotonic_ns'] for c in summary['cases'])
    ledger=[]; by_kind={}
    for r in api:
        request=r['request_serialized_bytes']; reply=r['reply_serialized_bytes']
        kind=mapping[r['kind']]
        by_kind.setdefault(kind,{'calls':0,'request_bytes':0,'reply_bytes':0})
        by_kind[kind]['calls']+=1;by_kind[kind]['request_bytes']+=request;by_kind[kind]['reply_bytes']+=reply
        links=[0]*8;links[2*(r['node']-1)]=request;links[2*(r['node']-1)+1]=reply
        ledger.append(ControlEvent(kind,(r['before_monotonic_ns']-origin)/1000,
                                   (r['after_monotonic_ns']-origin)/1000,tuple(links),
                                   run.name+'/control-events.jsonl','serialized_rpc'))
    ledger_totals=ledger_cost(ledger,8)
    assert sum(ledger_totals['control_per_link_bytes'])==sum(v['request_bytes']+v['reply_bytes'] for v in by_kind.values())
    frames=rows(run/'rpc-frame-counts.jsonl')
    # 抓包先于建网启动，首条 TCP 帧为 node 1 的就绪连接，节点端口依次递增。
    base_port=frames[0]['destination_port']
    server_ports={base_port+i:i for i in range(4)}
    physical=[0]*8
    for f in frames:
        if f['destination_port'] in server_ports:
            index=2*server_ports[f['destination_port']]
        elif f['source_port'] in server_ports:
            index=2*server_ports[f['source_port']]+1
        else: raise RuntimeError('出现未知控制服务器端口')
        physical[index]+=f['frame_bytes']
    assert sum(physical)==summary['control_capture']['loopback_outgoing_frame_bytes']
    # 不按 recvmsg 时间强行归因 RPC，TCP 延迟确认与排队可能越过 API 边界。
    wire_event=ControlEvent('unattributed',0,0,tuple(physical),run.name+'/rpc-frame-counts.jsonl')
    physical_totals=ledger_cost([wire_event],8)
    return {'run':run.name,'input_summary_sha256':sha(run/'summary.json'),'wire_checks':checks,
            'metered_rpc_by_kind':by_kind,'serialized_ledger_totals':ledger_totals,
            'captured_control_ledger_totals':physical_totals,'captured_control_ledger':[asdict(wire_event)],
            'loopback_captured_frame_bytes':summary['control_capture']['loopback_outgoing_frame_bytes'],
            'scope':'序列化账本仅含持久 Client 调用，初始化 CLI、TCP 建连、确认与分段另含于捕获总量；两者不可直接相加',
            'control_ledger':[asdict(e) for e in ledger]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs',type=Path,nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); output=a.output.resolve();output.mkdir(parents=True,exist_ok=False)
    sources=['src/orchestration/budget_model.py','src/orchestration/budget_oracle.py','src/orchestration/metrics.py',
             'experiments/check_budget_contract.py']
    started=time.monotonic()
    summary={'status':'running','run_id':output.name,'start_utc':datetime.now(timezone.utc).isoformat(),
             'versions':{'python':platform.python_version(),'numpy':numpy.__version__,'scipy':scipy.__version__},
             'synthetic_workload_seed':None,'randomness':'确定性给定轨迹和 MILP，没有随机工作负载',
             'synthetic_topology':'two sampling nodes on one common chain',
             'start_load_average':list(os.getloadavg()),
             'source_sha256':{n:sha(ROOT/n) for n in sources},'runs':[],'synthetic':[],
             'interpretation':'真实网络字节核对与合成离线下界分列，不是可部署控制方案或创新证据'}
    try:
        for run in a.runs:
            result=check_run(run)
            path=output/(run.name+'-control-ledger.json')
            path.write_text(json.dumps(result.pop('control_ledger'),ensure_ascii=False,indent=2)+'\n')
            summary['runs'].append(result)
        tasks=[Task(1,8.5,2,initial_generated_us=-1),Task(2,12,1,initial_generated_us=-1)]
        base=[Packet(i+1,1,(1,2),(t,t+.1),(t,t+.1),t+.2) for i,t in enumerate([2,6,10,14])]
        ledger=[ControlEvent('installation',-5,-1,(8,10),'手算合成假设，不是 RPC 测量'),
                ControlEvent('observation',1,2,(7,10),'手算合成假设，不是 RPC 测量')]
        for case in ['budget_source','control_shortfall','report_loss','report_delay','eligibility']:
            packets=list(base)
            if case=='report_loss': packets[1]=replace(packets[1],arrival_us=None)
            if case=='report_delay': packets[1]=replace(packets[1],arrival_us=26)
            if case=='eligibility': packets[1]=replace(packets[1],eligible_nodes=(2,))
            budget=Budget((64,84),186,(14,20) if case=='control_shortfall' else (15,20))
            for fmt in FORMATS.values():
                for mode in ['fixed','optional']:
                    result=solve(tasks,packets,0,0,18,budget,fmt,mode,control_events=ledger)
                    summary['synthetic'].append({'case':case,'format':fmt.name,'source_mode':mode,'result':result})
        inputs={'tasks':[asdict(t) for t in tasks],'base_packets':[asdict(p) for p in base],
                'window_us':[0,18],'budget':asdict(Budget((64,84),186,(15,20))),
                'control_ledger':[asdict(e) for e in ledger],
                'scope':'给定合成已收初始样本，未来轨迹已知，资格和控制账本固定，权限安装可实现性未证明'}
        (output/'synthetic-input.json').write_text(json.dumps(inputs,ensure_ascii=False,indent=2)+'\n')
        summary['status']='passed'
    except Exception as exc:
        summary.update(status='failed',failure=str(exc));raise
    finally:
        summary['finish_utc']=datetime.now(timezone.utc).isoformat()
        summary['wall_seconds']=time.monotonic()-started
        summary['finish_load_average']=list(os.getloadavg())
        summary['artifact_sha256']={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}
        (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':summary['status'],'wire_checks':sum(len(r['wire_checks']) for r in summary['runs']),
                      'synthetic_solves':len(summary['synthetic'])},ensure_ascii=False))


if __name__=='__main__': main()
