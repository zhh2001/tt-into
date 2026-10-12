#!/usr/bin/env python3
"""保留失败阳性对照，用发送前与 RPC 读回后边界核对源端时间。"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
rows=lambda p:[json.loads(x) for x in p.read_text().splitlines() if x]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs',nargs='+',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();output=a.output.resolve();output.mkdir(parents=True,exist_ok=False)
    result={'status':'running','start_utc':datetime.now(timezone.utc).isoformat(),
            'script_sha256':sha(Path(__file__)),'runs':[],
            'scope':'进程内 CLOCK_REALTIME 扰动正确性检查；宿主未改时钟，不是随机性能重复'}
    try:
        for run in a.runs:
            manifest=json.loads((run/'manifest.json').read_text())
            for n,h in manifest['source_sha256'].items(): assert sha(ROOT/n)==h,(run,n)
            runtime=manifest['runtime_clock']; legacy=runtime['legacy_positive_control']
            if not legacy: assert manifest['status']=='passed'
            inject=runtime['injected_events']
            if runtime['realtime_jumps_in_switch_only']:
                processes={e['process_id'] for e in inject}
                assert len(processes)==4
                assert all({e['realtime_shift_seconds'] for e in inject if e['process_id']==pid}=={-3600,3600} for pid in processes)
            target=output/run.name;target.mkdir()
            rpc=rows(run/'control-events.jsonl')
            series=[];compatibilities=[]
            for case in manifest['cases']:
                d=run/case['name']
                if not (d/'observations.jsonl').exists(): continue
                observations=rows(d/'observations.jsonl')
                sends={r['packet_id']:r for r in rows(d/'send.jsonl')}
                reads=[r for r in rpc if r['method']=='bm_register_read_all' and r['node']==1 and r['arguments']==['TTIngress.trace_source_time']
                       and r['after_monotonic_ns']>case['end_monotonic_ns']]
                assert reads
                read_after=min(r['after_monotonic_ns'] for r in reads)
                observed=[r for r in observations if r['source']['trace_source_count']>0]
                bound=[]
                previous=unwrapped=first_clock=first_send=None
                case_series=[]
                for r in sorted(observed,key=lambda x:sends[x['packet_id']]['send_before_monotonic_ns']):
                    raw=r['source']['trace_source_time'];sent=sends[r['packet_id']]['send_before_monotonic_ns']
                    if previous is None: unwrapped=raw;first_clock=raw;first_send=sent
                    else: unwrapped+=((raw-previous+(1<<47))%(1<<48))-(1<<47)
                    previous=raw
                    bound.append((sent-unwrapped*1000,read_after-unwrapped*1000))
                    case_series.append({'packet_id':r['packet_id'],'send_elapsed_s':(sent-first_send)/1e9,
                                        'source_elapsed_minus_send_elapsed_s':(unwrapped-first_clock)/1e6-(sent-first_send)/1e9})
                if bound:
                    lower=max(v[0] for v in bound);upper=min(v[1] for v in bound)
                    compatible=lower<=upper
                    compatibilities.append({'case':case['name'],'observed_packets':len(observed),
                                             'compatible':compatible,'gap_us':max(0,lower-upper)/1000,
                                             'bound':'发送前 <= ingress 时间事件 <= 对应诊断寄存器 RPC 读回后',
                                             'modulo':'48 bit 相邻差按最短有符号位移展开，仅用于本次短轨迹'})
                    if not legacy: assert compatible
                if case['name']=='shared': series=case_series
            if legacy:
                assert manifest['status']=='failed'
                assert any(not c['compatible'] for c in compatibilities)
                assert max(r['source_elapsed_minus_send_elapsed_s'] for r in series)-min(r['source_elapsed_minus_send_elapsed_s'] for r in series)>7000
                # 原失败状态原样保留，负面数据不走只接受 passed 的常规分析器。
                for name in ['packets.json','send.jsonl','observations.jsonl','destination.jsonl','collector.jsonl','destination.pcap','collector.pcap']:
                    shutil.copyfile(run/'shared'/name,target/name)
                (target/'original-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
            (target/'clock-series.json').write_text(json.dumps(series,ensure_ascii=False,indent=2)+'\n')
            result['runs'].append({'run_id':run.name,'input_manifest_sha256':sha(run/'manifest.json'),
                                   'original_status':manifest['status'],'legacy_positive_control':legacy,
                                   'cases':len(manifest['cases']),'input_packets':sum(c['input_packets'] for c in manifest['cases']),
                                   'received_packets':sum(c['received_packets'] for c in manifest['cases']),
                                   'injected_events':len(inject),'source_clock_checks':compatibilities})
        result['status']='passed'
    except Exception as exc:
        result.update(status='failed',failure=str(exc));raise
    finally:
        result['finish_utc']=datetime.now(timezone.utc).isoformat()
        result['artifact_sha256']={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}
        (output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'runs':len(result['runs'])}))


if __name__=='__main__': main()
