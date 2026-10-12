"""绑定最大配置交付、实际诊断、字体和后续训练冻结输入。"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from PIL import Image
from scalable_contract import ROOT,sha,source_files
from check_scale_preflight_delivery import check
from check_observation_wire import load,rows
from scale_training_contract import validate


def audit(analysis,figures,training,output):
    log=output/'unit-tests-final.txt'
    if not log.exists() or not log.read_text().rstrip().endswith('OK'):raise ValueError('完整回归没有通过')
    plan=load(training/'training-plan.json');validate(plan,True)
    if sha(training/'training-plan.json')!=load(training/'manifest.json')['artifact_sha256']['training-plan.json']:
        raise ValueError('训练冻结摘要不符')
    delivery=check(analysis)
    figure=load(figures/'manifest.json')
    if figure['data_sha256']!=sha(analysis/'summary.json'):raise ValueError('绘图来源改变')
    for name,digest in figure['artifacts'].items():
        if sha(figures/name)!=digest:raise ValueError('图片摘要改变')
    fonts=subprocess.run(['pdffonts',str(figures/'n5b-entry-diagnostics.pdf')],capture_output=True,text=True,check=True).stdout
    if 'TimesNewRomanPSMT' not in fonts or 'yes yes yes' not in fonts:raise ValueError('PDF字体没有嵌入')
    im=Image.open(figures/'n5b-entry-diagnostics.png')
    if min(im.info['dpi'])<299:raise ValueError('PNG分辨率不足')
    diagnostics=[]
    for row in load(analysis/'summary.json'):
        d=analysis/row['case'];case=load(d/'result.json')
        ds=rows(d/'decisions.jsonl');obs=rows(d/'report-observations.jsonl')
        ing=rows(d/'availability.jsonl');send=rows(d/'send.jsonl')
        rpc=rows(analysis/'rpc-operations.jsonl')[case['rpc_start_index']:case['rpc_stop_index']]
        solver=[(r['solver_finished_ns']-r['solver_started_ns'])/1e6 for r in ds if 'solver_finished_ns' in r]
        reads=[(r['read_finished_ns']-r['read_started_ns'])/1e6 for r in ing]
        final=load(d/'final-state.json')
        gates=load(d/'generation-bounds.json')
        diagnostics.append(dict(case=row['case'],solver_max_ms=max(solver,default=None),
            read_max_ms=max(reads,default=None),rpc_max_ms=max((r['after_monotonic_ns']-r['before_monotonic_ns'])/1e6 for r in rpc),
            source_lateness_max_ms=max(r['schedule_lateness_ns'] for r in send)/1e6,
            completed_observations=sum(not r['skipped'] for r in obs),skipped_observations=sum(r['skipped'] for r in obs),
            skipped_decisions=sum(r.get('skipped',False) for r in ds),
            final_enabled_positions=sum(bool(r['capacity']) for r in final),
            generation_clock_condition={n:r['compatible'] for n,r in gates['local_clock_offset'].items()},
            wall_clock_compatible=load(d/'window-costs.json')['wall_clock']['compatible'],
            observer_max_delay_ms=row['observer_max_delay_ms']))
    (output/'delivery-checks.json').write_text(json.dumps(delivery,indent=2)+'\n')
    (output/'diagnostics.json').write_text(json.dumps(diagnostics,ensure_ascii=False,indent=2)+'\n')
    (output/'pdf-fonts.txt').write_text(fonts)
    for name in source_files():
        target=output/'final-source'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    record=dict(status='passed',delivery=delivery,scope='确定性最大配置正确性，训练业务尚未生成',
        analysis_manifest_sha256=sha(analysis/'manifest.json'),figure_manifest_sha256=sha(figures/'manifest.json'),
        training_plan_sha256=sha(training/'training-plan.json'),training_cases=plan['expected_cases'],
        source_sha256={n:sha(ROOT/n) for n in source_files()},
        artifact_sha256={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()})
    (output/'manifest.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    return dict(status='passed',checks=delivery['checks'],training_cases=plan['expected_cases'])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('analysis','figures','training','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();print(json.dumps(audit(a.analysis,a.figures,a.training,a.output)))
