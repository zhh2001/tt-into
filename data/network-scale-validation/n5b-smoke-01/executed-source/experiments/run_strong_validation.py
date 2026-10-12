#!/usr/bin/env python3
"""真实执行固定路径筛选、布尔反馈、K=1 蓄水池与格式消融。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import shutil
from mininet.log import setLogLevel
from build_strong import ROOT, build, FORMATS
from topology import create_chain
from run_validation import configure, make_packet, load_events, finish_capture, software_version
from control.bmv2 import Client
from collector.codec import decode_frame as decode_plain
from collector.strong_codec import STATE_TYPE
from orchestration.strong import FlowFilter, BooleanFeedback, predict

SOURCES = ['src/p4/strong_int.p4','src/control/bmv2.py','src/collector/strong_codec.py',
           'src/collector/codec.py','src/orchestration/strong.py','experiments/build_strong.py',
           'experiments/run_strong_validation.py','experiments/strong_io.py','experiments/strong_rpc.py',
           'experiments/packet_io.py','experiments/topology.py','experiments/run_validation.py','experiments/build.py']
INGRESS_TRACES = ['trace_source_time','trace_source_count','trace_source_dev','trace_source_ratio',
                  'trace_source_active','trace_source_sampled']
EGRESS_TRACES = ['trace_arrival','trace_generated','trace_value','trace_decision','trace_count_before',
                 'trace_gate_index','trace_draw','trace_seen','trace_force']


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')


def capture(host, directory, name, serve=False):
    ready, stop = directory/(name+'.ready'), directory/(name+'.stop')
    log=open(directory/(name+'.log'),'w')
    cmd=[sys.executable,str(ROOT/'experiments/strong_io.py'),'capture','--iface',host.defaultIntf().name,
         '--pcap',str(directory/(name+'.pcap')),'--events',str(directory/(name+'.jsonl')),
         '--ready',str(ready),'--stop',str(stop)]
    if serve: cmd.append('--serve')
    process=host.popen(cmd,stdout=log,stderr=subprocess.STDOUT)
    deadline=time.monotonic()+5
    while not ready.exists():
        if process.poll() is not None or time.monotonic()>deadline:
            process.terminate(); process.wait(timeout=3); log.close()
            raise RuntimeError('抓包启动失败 '+name)
        time.sleep(0.01)
    return process,stop,log


def scenarios(fmt):
    names=['shared','ratio','dev_filter','feedback_base','feedback_pro','mtu']
    if fmt['reservoir']: names+=['uniform','weighted','dev_uniform','dev_weighted']
    return names


def setup(clients, mode, scope, thresholds, capacities, weights):
    for node,client in enumerate(clients[:3]):
        for reg in ['last_timestamp','has_sample','force_sampling']:
            client.reset('TTEgress.'+reg)
        for reg in ['packet_count','arrival_last','arrival_valid','arrival_dev']:
            client.reset('TTIngress.'+reg)
        client.default('TTEgress.gate_policy','TTEgress.choose_scope',[(int(scope=='port'),1)])
        client.default('TTEgress.selection_policy','TTEgress.select_mode',[(mode,8),(weights[node],8)])
        for flow in [1,2]:
            client.write('TTEgress.time_threshold',258 if scope=='port' else flow,thresholds[node])
            client.write('TTEgress.max_count',flow,capacities[node])
            client.write('TTIngress.ideal_interval',flow,100000)
        if node==0:
            for flow in [1,2]:
                client.entry('TTIngress.sampling_policy',flow,32,'TTIngress.sampling',[(1,1),(1,32)])
    clients[3].write('TTEgress.drop_report',0,0)
    clients[3].reset('TTEgress.trace_drop') if hasattr(clients[3],'diagnostics') and clients[3].diagnostics else None


def verify(directory, packets, fmt, config, clients, diagnostics):
    destination=load_events(directory/'destination.jsonl')
    received=[p for p in destination if p.get('kind')=='frame' and p.get('src')=='10.0.0.1']
    got={p['packet_id']:p for p in received}
    report_rows=[p for p in load_events(directory/'collector.jsonl') if p.get('ether_type')==STATE_TYPE]
    reports={p['packet_id']:p for p in report_rows}
    apps=[p['payload_hex'] for p in destination if p.get('kind')=='udp_application']
    errors=[]
    if len(received)!=len(packets) or len(got)!=len(packets) or len(reports)!=len(report_rows):
        errors.append('业务或报告数量重复、缺失')
    for p in packets:
        row=got.get(p['packet_id'])
        original=decode_plain(bytes.fromhex(p['frame_hex']))
        if not row or row['transport_hex']!=original['transport_hex'] or row['ip_length']!=p['ip_length'] or row['ttl']!=60 or not row['ip_checksum_ok'] or p['payload_hex'] not in apps:
            errors.append('业务恢复错误 '+str(p['packet_id']))
    for row in report_rows:
        if row['version']!=fmt['version'] or row['transport_hex'] or not row['ip_checksum_ok'] or row['frame_length']!=34+fmt['header']+fmt['record']*row['count']:
            errors.append('导出格式错误 '+str(row['packet_id']))
        if config['mode'] and row['count']>1: errors.append('蓄水池超出 K=1')
    result={'errors':errors,'input_packets':len(packets),'received_packets':len(got),'report_packets':len(reports)}
    if diagnostics:
        source={reg:clients[0].read_all('TTIngress.'+reg) for reg in INGRESS_TRACES}
        traces=[{reg:c.read_all('TTEgress.'+reg) for reg in EGRESS_TRACES} for c in clients[:3]]
        drop=clients[3].read_all('TTEgress.trace_drop')
        expected=predict(packets,traces,source,fmt,**config)
        errors.extend(expected['errors'])
        exported=0
        for p in packets:
            pid,idx=p['packet_id'],p['packet_id']&4095
            row=expected['packets'][pid]
            present=bool(row['records'] and not drop[idx])
            if present!=(pid in reports) or present and row['records']!=reports[pid]['records']:
                errors.append('独立记录核对错误 '+str(pid))
            if present: exported+=34+fmt['header']+fmt['record']*len(row['records'])
        for node,c in enumerate(clients[:3]):
            actual=c.read_all('TTEgress.last_timestamp')
            valid=c.read_all('TTEgress.has_sample')
            for index,value in enumerate(actual):
                if value!=expected['gates'][node].get(index,0) or valid[index]!=int(index in expected['gates'][node]):
                    errors.append(f'最终门控状态错误 {node+1}/{index}')
        observation=[]
        for p in packets:
            pid,idx=p['packet_id'],p['packet_id']&4095
            observation.append({'packet_id':pid,'source':{k:source[k][idx] for k in INGRESS_TRACES},
                                'nodes':[{k:tr[k][idx] for k in EGRESS_TRACES} for tr in traces],
                                'sink_report_dropped':drop[idx]})
        (directory/'observations.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in observation))
        write_json(directory/'registers.json',{'source':source,'nodes':traces,'drop':drop})
        result.update(successful_insertions_per_node=expected['successful_insertions_per_node'],
                      expected_inband_per_link_bytes=expected['inband_per_link_bytes'],expected_export_bytes=exported)
    histogram=[0,0,0]
    for row in report_rows:
        for record in row['records']: histogram[record['switch_id']-1]+=1
    result['reported_samples_per_node']=histogram
    drops=max((r.get('socket_drop_counter',0) for r in destination+report_rows),default=0)
    result['capture_socket_drop_counter']=drops
    if drops: errors.append('收包套接字发生丢包')
    result['status']='passed' if not errors else 'failed'
    return result


def run_case(output, name, number, fmt, hosts, clients, diagnostics):
    source,destination,collector=hosts
    directory=output/name
    directory.mkdir()
    config={'mode':int(name in ['uniform','dev_uniform'])+2*int(name in ['weighted','dev_weighted']),'scope':'port' if name.startswith('feedback') or name=='shared' else 'flow',
            'thresholds':[100000,300000,1] if name=='shared' else [100000]*3 if name.startswith('feedback') else [1]*3,
            'capacities':[3]*3 if name.startswith('feedback') else [1]*3,'weights':[3,2,1]}
    setup(clients,**config)
    if diagnostics:
        for c in clients[:3]:
            for reg in EGRESS_TRACES: c.reset('TTEgress.'+reg)
        for reg in INGRESS_TRACES: clients[0].reset('TTIngress.'+reg)
    base=number*2048
    packets=[]
    if name=='shared':
        packets=[make_packet(base+2*i+f,flow=f,offset_ms=i*200+(5 if f==2 else 0)) for i in range(12) for f in [1,2]]
    elif name in ['uniform','weighted']:
        packets=[make_packet(base+i+1,offset_ms=i*2) for i in range(768)]
    elif name=='ratio':
        clients[0].entry('TTIngress.sampling_policy',1,32,'TTIngress.sampling',[(1,1),(3,32)])
        packets=[make_packet(base+i+1,offset_ms=i*20) for i in range(12)]
    elif name=='mtu':
        sizes=[1500-fmt['header']-fmt['record'],1501-fmt['header']-fmt['record'],1500-fmt['header'],1501-fmt['header'],1499,1500]
        packets=[make_packet(base+i+1,offset_ms=i*30,ip_length=n) for i,n in enumerate(sizes)]
    elif name.startswith('dev_'):
        for flow in [1,2]: clients[0].entry('TTIngress.sampling_policy',flow,32,'TTIngress.sampling',[(0,1),(1,32)])
        events=[(i*25,1) for i in range(96)]+[(group*200+i*5,2) for group in range(12) for i in range(8)]
        packets=[make_packet(base+i+1,flow=f,offset_ms=t) for i,(t,f) in enumerate(sorted(events))]
    elif name.startswith('feedback'):
        clients[3].write('TTEgress.drop_report',0,1,kind='loss_injection')
        packets=[make_packet(base+i+1,offset_ms=i*20) for i in range(60)]
    write_json(directory/'packets.json',packets)
    handles=[]
    sender=None
    controller=[]
    try:
        handles=[capture(destination,directory,'destination',True),capture(collector,directory,'collector')]
        start=time.monotonic_ns()+600000000
        ready=directory/'sender.ready'
        sender_log=open(directory/'sender.log','w')
        sender=source.popen([sys.executable,str(ROOT/'experiments/strong_io.py'),'send',
                            '--iface',source.defaultIntf().name,'--packets',str(directory/'packets.json'),
                            '--events',str(directory/'send.jsonl'),'--ready',str(ready),'--start-ns',str(start)],
                           stdout=sender_log,stderr=subprocess.STDOUT)
        filter_state=FlowFilter(100000,30,60000)
        feedback=BooleanFeedback({n:100000 for n in [1,2,3]})
        latest={n:start for n in [1,2,3]}
        next_check=start+400000000
        loss_restored=False
        seen_reports=set()
        end=start+max(p['offset_ns'] for p in packets)+450000000
        while time.monotonic_ns()<end:
            now=time.monotonic_ns()
            if name.startswith('dev_') and now>=next_check and next_check<=start+2400000000:
                for flow in [1,2]:
                    cnt=clients[0].read('TTIngress.packet_count',flow)
                    dev=clients[0].read('TTIngress.arrival_dev',flow)
                    measured=time.monotonic_ns()
                    decision=filter_state.update(flow,cnt,dev,measured,start)
                    decision['observed_monotonic_ns']=measured
                    controller.append(decision)
                    clients[0].entry('TTIngress.sampling_policy',flow,32,'TTIngress.sampling',
                                     [(int(decision['eligible']),1),(decision['ratio_n'],32)],kind='online_update')
                next_check+=400000000
            if name.startswith('feedback') and now>=start:
                if not loss_restored and now-start>=400000000:
                    clients[3].write('TTEgress.drop_report',0,0,kind='loss_injection')
                    loss_restored=True
                events=load_events(directory/'collector.jsonl')
                for row in events:
                    if row.get('ether_type')==STATE_TYPE and row['packet_id'] not in seen_reports:
                        seen_reports.add(row['packet_id'])
                        for record in row['records']: latest[record['switch_id']]=row['receive_monotonic_ns']
                if name=='feedback_pro':
                    for node,value in feedback.tick(now,latest):
                        before=time.monotonic_ns()
                        clients[node-1].write('TTEgress.force_sampling',2,int(value),kind='online_update')
                        controller.append({'node':node,'value':int(value),'decision_monotonic_ns':now,
                                           'before_monotonic_ns':before,'after_monotonic_ns':time.monotonic_ns(),
                                           'latest_received_monotonic_ns':latest[node]})
            time.sleep(0.01)
        sender.wait(timeout=3)
        sender_log.close()
        if sender.returncode: raise RuntimeError('发送进程失败')
        for handle in handles: finish_capture(handle)
        handles=[]
        result=verify(directory,packets,fmt,config,clients,diagnostics)
        if name=='ratio' and result['report_packets']!=4: result['errors'].append('1:3 未产生四个报告')
        if name.startswith('dev_'):
            last={f:next(r for r in reversed(controller) if r['flow']==f) for f in [1,2]}
            if not last[1]['eligible'] or not last[2]['removed']: result['errors'].append('平滑与突发流未按预设条件区分')
        if name=='feedback_pro':
            for node in [1,2,3]:
                values=[r['value'] for r in controller if r['node']==node]
                if not any(values[i:i+2]==[1,0] for i in range(len(values)-1)):
                    result['errors'].append(f'反馈未完成开启与退出 {node}')
        result.update(name=name,configuration=config,controller=controller,
                      start_monotonic_ns=start,end_monotonic_ns=end,
                      status='passed' if not result['errors'] else 'failed')
        write_json(directory/'result.json',result)
        return result
    finally:
        if sender and sender.poll() is None: sender.terminate(); sender.wait(timeout=3)
        for handle in handles: finish_capture(handle)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--format',choices=FORMATS,default='common')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--cases',nargs='+')
    p.add_argument('--ordinary',action='store_true')
    a=p.parse_args()
    if os.geteuid(): p.error('需要 sudo 运行')
    setLogLevel('warning')
    output=a.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    archive=output/'executed-source'
    for name in SOURCES:
        target=archive/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,target)
    manifest={'run_id':output.name,'status':'running','format':FORMATS[a.format],
              'format_name':a.format,'diagnostics':not a.ordinary,'topology':'3 sampling nodes + sink',
              'workload_seed':0,'data_plane_rng_seed':None,'data_plane_rng':'v1model random，保留实际抽签，不宣称可配置种子',
              'source_sha256':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in SOURCES},
              'start_utc':datetime.now(timezone.utc).isoformat(),'start_load_average':list(os.getloadavg()),
              'versions':{'p4c':software_version(['p4c-bm2-ss','--version']),'bmv2':software_version(['simple_switch','--version']),
                          'python':sys.version.split()[0],'mininet':software_version(['mn','--version'])},'cases':[]}
    network,rpc_handle=None,None
    clients=[]
    log=[]
    try:
        base_port=30000+(os.getpid()%1000)*10
        ready,stop=output/'rpc.ready',output/'rpc.stop'
        rpc_log=open(output/'rpc.log','w')
        process=subprocess.Popen([sys.executable,str(ROOT/'experiments/strong_rpc.py'),'--events',str(output/'rpc-events.jsonl'),
                                  '--pcap',str(output/'rpc.pcap'),'--ready',str(ready),'--stop',str(stop),
                                  '--ports',*[str(base_port+i) for i in range(1,5)]],stdout=rpc_log,stderr=subprocess.STDOUT)
        rpc_handle=process,stop,rpc_log
        deadline=time.monotonic()+5
        while not ready.exists():
            if process.poll() is not None or time.monotonic()>deadline: raise RuntimeError('控制抓包启动失败')
            time.sleep(0.01)
        network,source,destination,collector,switches=create_chain(build(a.format,not a.ordinary),output,3,
                                                                 f'st{os.getpid()%65536:04x}',base_port,os.getpid()*10)
        configure(switches,3)
        clients=[Client(base_port+i,i,log) for i in range(1,5)]
        for c in clients: c.diagnostics=not a.ordinary
        names=scenarios(FORMATS[a.format])
        if a.cases:
            if set(a.cases)-set(names): raise ValueError('格式不支持指定检查')
            names=[n for n in names if n in a.cases]
        for number,name in enumerate(names):
            result=run_case(output,name,number,FORMATS[a.format],(source,destination,collector),clients,not a.ordinary)
            manifest['cases'].append(result)
            write_json(output/'manifest.json',manifest)
            print(a.format,name,result['status'],result['reported_samples_per_node'],result['errors'][:4],flush=True)
        manifest['status']='passed' if all(c['status']=='passed' for c in manifest['cases']) else 'failed'
    except Exception as exc:
        manifest.update(status='failed',failure=str(exc))
        (output/'failure.txt').write_text(traceback.format_exc())
        print('失败',exc,flush=True)
    finally:
        for client in clients: client.close()
        if network: network.stop()
        if rpc_handle: finish_capture(rpc_handle)
        (output/'control-events.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in log))
        manifest.update(finish_utc=datetime.now(timezone.utc).isoformat(),finish_load_average=list(os.getloadavg()))
        write_json(output/'manifest.json',manifest)
    return int(manifest['status']!='passed')


if __name__=='__main__': raise SystemExit(main())
