"""绑定独立业务、执行与分析快照、完整回归和真正TNR图片。"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
from PIL import Image
from analyze_observer_training import write
from check_matched_network import load,sha
from check_rolling_reference import same
from observer_comparison_contract import METHOD_PATH,METHOD_SHA256,validate as validate_plan
from plot_observer_comparison import NAMES

ROOT=Path(__file__).resolve().parents[1]


def validate(analysis,figures,checks,unit_log,output):
    m,f,audit=load(analysis/'manifest.json'),load(figures/'manifest.json'),load(checks)
    if audit['status']!='passed' or audit['cases']!=256 or audit['analysis_manifest_sha256']!=sha(analysis/'manifest.json'):
        raise ValueError('独立验收未绑定完整比较')
    if f['analysis_manifest_sha256']!=sha(analysis/'manifest.json') or f['independent_checks_sha256']!=sha(checks):
        raise ValueError('图片未绑定实际验收')
    plan=load(analysis/'comparison-plan.json'); validate_plan(plan)
    same(sha(METHOD_PATH),METHOD_SHA256,'原冻结方法');same(sha(analysis/'method-contract.json'),METHOD_SHA256,'实际执行方法')
    same(m['fresh_business_seeds'],plan['fresh_business_seeds'],'四条独立业务')
    same((m['independent_business_units'],m['deployment_blocks'],m['expected_cases']),(4,2,256),'完整评价分母')
    same(load(analysis/'seed-audit.json'),plan['seed_audit'],'首例前消费证据')
    count=5
    for directory,record in ((analysis,m),(figures,f)):
        for name,digest in record['artifact_sha256'].items():
            if sha(directory/name)!=digest:raise ValueError('产物摘要改变')
            count+=1
        for key,folder in (('source_sha256','executed-source'),('analysis_source_sha256','executed-analysis-source')):
            for name,digest in record.get(key,{}).items():
                if sha(ROOT/name)!=digest or sha(directory/folder/name)!=digest:raise ValueError('源码及实际快照改变')
                count+=2
    text=unit_log.read_text();match=re.search(r'Ran (\d+) tests in [\d.]+s\s+OK\s*$',text)
    if not match or int(match.group(1))<470:raise ValueError('最终完整回归缺失')
    images,fonts={},{}
    for name in NAMES:
        actual=subprocess.check_output(['pdffonts',str(figures/(name+'.pdf'))],text=True)
        if actual!=(figures/(name+'-fonts.txt')).read_text() or not actual.splitlines()[2:] or any(
            'TimesNewRoman' not in r or ' yes ' not in r for r in actual.splitlines()[2:]):raise ValueError('实际PDF字体不符')
        with Image.open(figures/(name+'.png')) as im:
            dpi=im.info.get('dpi')
            if not dpi or any(abs(v-300)>.01 for v in dpi):raise ValueError('PNG不是300dpi')
            images[name]=dict(pixels=list(im.size),dpi=list(dpi))
        fonts[name]=actual.splitlines()[2:];count+=4
    if f['font_name']!='Times New Roman' or sha(Path(f['font_file']))!=f['font_sha256']:raise ValueError('真实字体摘要改变')
    output.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(checks,output/'independent-checks.json');shutil.copyfile(unit_log,output/'final-unit-tests.txt')
    result=dict(status='passed',cases=256,independent_business_units=4,deployment_blocks=2,
        unit_tests=int(match.group(1)),delivery_checks=count,network_checks=audit['network_checks'],
        analysis_checks=audit['analysis_checks'],pair_checks=audit['pair_checks'],
        method_contract_sha256=METHOD_SHA256,analysis_manifest_sha256=sha(analysis/'manifest.json'),
        figure_manifest_sha256=sha(figures/'manifest.json'),images=images,embedded_fonts=fonts,
        scope='完整256例、224配对、112独立业务配对组和128部署重复组')
    write(output/'delivery-checks.json',result)
    source='experiments/validate_observer_comparison_delivery.py';target=output/'executed-source'/source
    target.parent.mkdir(parents=True);shutil.copyfile(ROOT/source,target)
    write(output/'manifest.json',dict(status='passed',findings=result,source_sha256={source:sha(ROOT/source)},
        artifact_sha256={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}))
    print(__import__('json').dumps({k:result[k] for k in ('status','cases','unit_tests','delivery_checks','network_checks','analysis_checks','pair_checks')}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('analysis','figures','checks','unit-log','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();validate(a.analysis,a.figures,a.checks,a.unit_log,a.output)
