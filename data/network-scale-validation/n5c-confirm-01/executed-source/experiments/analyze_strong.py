"""按唯一包 ID 核对链路成本，以单宿主因果区间评价年龄。"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sys
from scapy.utils import RawPcapReader
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from collector.strong_codec import decode_frame,STATE_TYPE
from orchestration.metrics import age_metrics
from orchestration.model import Task


def rows(path): return [json.loads(line) for line in path.read_text().splitlines() if line]


def clock_diagnostics(directory, reports):
    sends={p['packet_id']:p for p in rows(directory/'send.jsonl')}
    walls=[p['wall_minus_monotonic_ns'] for p in sends.values()]
    def intersect(bounds):
        if not bounds: return {'observed':False,'compatible':None}
        lower=max(x[0] for x in bounds)
        upper=min(x[1] for x in bounds)
        return {'observed':True,'compatible':lower<=upper,
                'intersection_width_us':max(0,upper-lower)/1000,
                'incompatible_gap_us':max(0,lower-upper)/1000}
    result={'send_wall_monotonic_offset_estimate_variation_ns':max(walls)-min(walls),
            'reported_nodes':{},'source_arrival':{'observed':False,'compatible':None},
            'interpretation':'恒偏移可相容性核对，不证明同步或持续稳定；单调域因果年龄不依赖该假设'}
    for node in [1,2,3]:
        bounds=[(sends[pid]['send_before_monotonic_ns']-r['generated_us']*1000,
                 report['receive_monotonic_ns']-r['generated_us']*1000)
                for pid,report in reports.items() for r in report['records'] if r['switch_id']==node]
        result['reported_nodes'][str(node)]=intersect(bounds)
    if (directory/'observations.jsonl').exists():
        destination={r['packet_id']:r for r in rows(directory/'destination.jsonl')
                     if r.get('kind')=='frame' and r.get('src')=='10.0.0.1'}
        bounds=[(sends[r['packet_id']]['send_before_monotonic_ns']-r['source']['trace_source_time']*1000,
                 destination[r['packet_id']]['receive_monotonic_ns']-r['source']['trace_source_time']*1000)
                for r in rows(directory/'observations.jsonl')]
        result['source_arrival']=intersect(bounds)
    result['incompatible_local_clock_observed']=any(v['compatible'] is False
        for v in [result['source_arrival'],*result['reported_nodes'].values()])
    return result


def ages(case, directory, packets, reports):
    if case['name'] not in ['shared','feedback_base','feedback_pro']:
        return {'evaluated':False,'reason':'本场景用于选择、统计或格式边界检查'}
    sends={p['packet_id']:p for p in rows(directory/'send.jsonl')}
    first=packets[8]['packet_id'] if case['name']=='shared' else packets[0]['packet_id']
    origin=sends[first]['send_before_monotonic_ns']
    end=(max(p['send_before_monotonic_ns'] for p in sends.values())+200000000-origin)/1000
    sample_bounds=[]
    values=[]
    for node,deadline in enumerate([250000,450000,650000],1):
        low,high=[],[]
        widths=[]
        for report in reports.values():
            if any(r['switch_id']==node for r in report['records']):
                pid=report['packet_id']
                lower=(sends[pid]['send_before_monotonic_ns']-origin)/1000
                arrival=(report['receive_monotonic_ns']-origin)/1000
                if lower>arrival: raise RuntimeError('单调时钟因果边界不相容')
                low.append((lower,arrival))
                high.append((arrival,arrival))
                widths.append(arrival-lower)
                sample_bounds.append({'packet_id':pid,'node':node,'generation_lower_us':lower,
                                      'generation_upper_us':arrival,'availability_us':arrival})
        pessimistic=age_metrics(Task(node,deadline),low,0,end)
        optimistic=age_metrics(Task(node,deadline),high,0,end)
        values.append({'node':node,'deadline_us':deadline,
                       'violation_lower':optimistic['violation_fraction'],'violation_upper':pessimistic['violation_fraction'],
                       'unknown_fraction':pessimistic['unknown_fraction'],
                       'p95_age_lower_us':optimistic['p95_age_us'],'p95_age_upper_us':pessimistic['p95_age_us'],
                       'maximum_causal_interval_width_us':max(widths,default=None),
                       'fulfilled_by_upper_age':pessimistic['fulfilled']})
    return {'evaluated':True,'origin_packet_id':first,'window_us':[0,end],'tasks':values,'samples':sample_bounds,
            'clock_domain':'同一 Linux 宿主 CLOCK_MONOTONIC 的发送前与采集 recvmsg 后边界',
            'generation_bound':'发送前 <= 真实队列快照生成事件 <= 采集 recvmsg 完成',
            'switch_timestamps_used_for_cross_device_age':False,
            'availability':'采集进程取到报告的时刻，保留实际前史',
            'fulfilled_tasks':sum(r['fulfilled_by_upper_age'] for r in values)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    run,output=a.run.resolve(),a.output.resolve()
    if output.exists() or run==output or run in output.parents or output in run.parents:
        raise RuntimeError('派生目录须独立且不存在')
    summary=json.loads((run/'manifest.json').read_text())
    if summary['status']!='passed': raise RuntimeError('原始运行未通过')
    for name,digest in summary['source_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:
            raise RuntimeError('测量源码变化 '+name)
    summary['analysis_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    summary['analysis_dependencies']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                                      for name in ['src/orchestration/model.py','src/orchestration/metrics.py']}
    cases={c['name']:c for c in summary['cases']}
    inputs={}
    per_case={}
    for name in cases:
        directory=run/name
        packets=json.loads((directory/'packets.json').read_text())
        reports={r['packet_id']:r for r in rows(directory/'collector.jsonl') if r.get('ether_type')==STATE_TYPE}
        for packet in packets:
            pid=packet['packet_id']
            if pid in inputs: raise RuntimeError('全过程包 ID 重复')
            inputs[pid]=name,packet
        cases[name]['age']=ages(cases[name],directory,packets,reports)
        cases[name]['clock_diagnostics']=clock_diagnostics(directory,reports)
        per_case[name]={'inband_per_link_bytes':[0,0,0],'export_bytes':0,
                        'export_bytes_with_minimum_ethernet_padding':0,'frames':[0]*5,'wire':[]}
    total=maximum=0
    for path in sorted(run.glob('node-*/*_out.pcap')):
        node=int(path.parent.name.split('-')[1])
        if path.name.endswith('-eth2_out.pcap'): edge=node-1
        elif node==4 and path.name.endswith('-eth3_out.pcap'): edge=4
        else: continue
        if not path.stat().st_size: continue
        with closing(RawPcapReader(str(path))) as reader:
            for frame,_ in reader:
                row=decode_frame(frame)
                if not row or row['src']!='10.0.0.1': continue
                if not row['ip_checksum_ok'] or row['ip_length']>1500: raise RuntimeError('链路校验和或 MTU 错误')
                pid=row['packet_id']
                if pid not in inputs: raise RuntimeError('链路存在未知包')
                name,original=inputs[pid]
                cost=per_case[name]
                extra=row['ip_length']-original['ip_length']
                if edge<3:
                    if extra<0: raise RuntimeError('带内长度缩短')
                    cost['inband_per_link_bytes'][edge]+=extra
                elif edge==3:
                    if extra!=0: raise RuntimeError('目的端仍有元数据')
                else:
                    if row['ether_type']!=STATE_TYPE: raise RuntimeError('未知导出帧')
                    cost['export_bytes']+=len(frame)
                    cost['export_bytes_with_minimum_ethernet_padding']+=max(60,len(frame))
                cost['frames'][edge]+=1
                cost['wire'].append({'packet_id':pid,'node':node,'port':3 if edge==4 else 2,
                                     'ip_bytes':row['ip_length'],'frame_bytes':len(frame),'added_bytes':extra if edge<4 else len(frame),
                                     'count':row.get('count',0),'checksum_ok':True})
                total+=1
                maximum=max(maximum,row['ip_length'])
    output.mkdir(parents=True)
    hashes={}
    for name,case in cases.items():
        cost=per_case[name]
        if cost['frames'][:4]!=[case['input_packets']]*4 or cost['frames'][4]!=case['report_packets']:
            raise RuntimeError('链路数量不符 '+name)
        if summary['diagnostics'] and (cost['inband_per_link_bytes']!=case['expected_inband_per_link_bytes'] or cost['export_bytes']!=case['expected_export_bytes']):
            raise RuntimeError('实测字节不符 '+name)
        directory=output/name
        directory.mkdir()
        for file in ['packets.json','send.jsonl','destination.jsonl','collector.jsonl','destination.pcap','collector.pcap','observations.jsonl']:
            source=run/name/file
            if source.exists(): shutil.copyfile(source,directory/file)
        (directory/'wire-observations.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in cost.pop('wire')))
        cost['total_byte_hops']=sum(cost['inband_per_link_bytes'])+cost['export_bytes']
        cost['total_byte_hops_with_minimum_ethernet_padding']=sum(cost['inband_per_link_bytes'])+cost['export_bytes_with_minimum_ethernet_padding']
        cost['padding_interpretation']='按捕获导出帧不足 60 B 时补齐推导，不是硬件测量；原业务帧均大于 60 B'
        cost['scope']='全部本场景包，不含另列的控制通信；不含 FCS、前导码及物理间隔'
        case['measured_cost']=cost
    shutil.copyfile(run/'control-events.jsonl',output/'control-events.jsonl')
    shutil.copyfile(run/'rpc-events.jsonl',output/'rpc-frame-counts.jsonl')
    rpc=rows(run/'rpc-events.jsonl')
    summary['control_capture']={'loopback_outgoing_frame_bytes':sum(r['frame_bytes'] for r in rpc),
                                'socket_drop_counter':max((r['socket_drop_counter'] for r in rpc),default=0),
                                'scope':'本机 Thrift/TCP 帧含 GSO，非部署控制网络；不公开载荷'}
    summary['wire_checks']={'passed':True,'checked_frames':total,'maximum_ip_length':maximum}
    for path in output.rglob('*'):
        if path.is_file(): hashes[str(path.relative_to(output))]=hashlib.sha256(path.read_bytes()).hexdigest()
    summary['public_artifact_sha256']=hashes
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'run':summary['run_id'],'cases':len(cases),'frames':total,
                      'age_fulfilled':{n:c['age'].get('fulfilled_tasks') for n,c in cases.items()}},ensure_ascii=False))


if __name__=='__main__': main()
