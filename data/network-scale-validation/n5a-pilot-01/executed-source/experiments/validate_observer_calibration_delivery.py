"""绑定控制器目标动态校准、独立全局参数排序、完整回归和TNR导出。"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess

from PIL import Image
from analyze_observer_training import write
from check_matched_network import load, sha

ROOT = Path(__file__).resolve().parents[1]


def check_methods(analysis,checks,methods):
    from check_rolling_reference import same
    m=load(methods/'manifest.json');contract=load(methods/'method-contract.json')
    plan=load(analysis/'calibration-plan.json');frozen=load(analysis/'frozen-parameters.json')
    count=0
    for name,digest in m['artifact_sha256'].items():
        same(sha(methods/name),digest,'方法冻结产物');count+=1
    for name,digest in m['source_sha256'].items():
        same(sha(ROOT/name),digest,'方法冻结源码');same(sha(methods/'executed-source'/name),digest,'实际方法冻结快照');count+=2
    same(contract['calibration_manifest_sha256'],sha(analysis/'manifest.json'),'方法来自实际校准')
    same(contract['calibration_checks_sha256'],sha(checks),'方法冻结独立验收')
    same(contract['primary_observer'],'controller','共同控制器主目标')
    same((contract['observation_period_us'],contract['decision_period_us']),(100000,1000000),'共同观察与决策')
    same((contract['fresh_business_seeds'],contract['fresh_inputs_generated']),(None,False),'新业务尚未生成')
    same((contract['fresh_business_count'],contract['deployment_blocks'],contract['expected_cases']),(4,2,256),'完整下一阶段比较分母')
    same(contract['profiles'],[dict(scenario=r['scenario'],budget_level=r['budget_level'],initial_config=r['selected_config']) for r in plan['common_initialization']],'全部方法共同初始化')
    same(contract['budget'],plan['protocol']['budget'],'六层预算不变')
    same(set(contract['methods']),set(plan['methods']),'全部八方法')
    for method,setting in contract['methods'].items():
        winner=next((r for r in frozen if r['method']==method),None)
        index=winner['selected_parameter'] if winner else None
        values=[e for e in plan['execution_order'] if e['method']==method and e['parameter_index']==index]
        same(setting['parameter_index'],index,'跨场景全局参数');same(setting['adaptive'],values[0]['adaptive'],'实际全局值')
        same(len(values),8,'冻结值完整八例');count+=3
    return count+9


def validate(analysis, figures, checks, unit_log, output, methods):
    m, f, audit = load(analysis / 'manifest.json'), load(figures / 'manifest.json'), load(checks)
    if audit['status'] != 'passed' or audit['cases'] != 184 or audit['analysis_manifest_sha256'] != sha(analysis / 'manifest.json'):
        raise ValueError('独立验收没有绑定实际试点')
    if f['analysis_manifest_sha256'] != sha(analysis / 'manifest.json') or f['independent_checks_sha256'] != sha(checks):
        raise ValueError('图片没有绑定实际分析验收')
    count = check_methods(analysis,checks,methods)
    for directory, record in ((analysis, m), (figures, f)):
        for name, digest in record['artifact_sha256'].items():
            if sha(directory / name) != digest: raise ValueError('产物摘要改变')
            count += 1
        for key, folder in (('source_sha256', 'executed-source'), ('analysis_source_sha256', 'executed-analysis-source')):
            for name, digest in record.get(key, {}).items():
                if sha(ROOT / name) != digest or sha(directory / folder / name) != digest: raise ValueError('源码、实际快照和交付不一致')
                count += 2
    log = unit_log.read_text(); match = re.search(r'Ran (\d+) tests in [\d.]+s\s+OK\s*$', log)
    if not match or int(match.group(1)) < 450: raise ValueError('最终完整回归缺失')
    images, fonts = {}, {}
    for name in ('controller-dynamic-calibration', 'global-parameter-calibration'):
        actual = subprocess.check_output(['pdffonts', str(figures / (name + '.pdf'))], text=True)
        if actual != (figures / (name + '-fonts.txt')).read_text() or not actual.splitlines()[2:] or any(
                'TimesNewRoman' not in r or ' yes ' not in r for r in actual.splitlines()[2:]):
            raise ValueError('实际PDF字体与证据不一致')
        with Image.open(figures / (name + '.png')) as im:
            dpi = im.info.get('dpi')
            if not dpi or any(abs(v-300) > .01 for v in dpi): raise ValueError('PNG不是300dpi')
            images[name] = {'pixels': list(im.size), 'dpi': list(dpi)}
        fonts[name] = actual.splitlines()[2:]; count += 4
    if f['font_name'] != 'Times New Roman' or sha(Path(f['font_file'])) != f['font_sha256']: raise ValueError('真实字体文件摘要不符')
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(checks, output / 'independent-checks.json'); shutil.copyfile(unit_log, output / 'final-unit-tests.txt')
    result = {'status': 'passed', 'cases': 184, 'unit_tests': int(match.group(1)), 'delivery_checks': count,
        'network_checks': audit['network_checks'], 'analysis_checks': audit['analysis_checks'], 'selection_checks': audit['selection_checks'], 'pair_checks': audit['pair_checks'],
        'method_contract_sha256':sha(methods/'method-contract.json'), 'analysis_manifest_sha256': sha(analysis / 'manifest.json'), 'figure_manifest_sha256': sha(figures / 'manifest.json'),
        'images': images, 'embedded_fonts': fonts, 'scope': '184例、十八参数评分及完整静态配对验收'}
    write(output / 'delivery-checks.json', result)
    source = 'experiments/validate_observer_calibration_delivery.py'; target = output / 'executed-source' / source
    target.parent.mkdir(parents=True); shutil.copyfile(ROOT / source, target)
    write(output / 'manifest.json', dict(status='passed', findings=result, source_sha256={source: sha(ROOT / source)},
        artifact_sha256={str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}))
    print(__import__('json').dumps({k: result[k] for k in ('status', 'cases', 'unit_tests', 'delivery_checks', 'network_checks', 'analysis_checks', 'selection_checks')}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('analysis', 'figures', 'checks', 'unit-log', 'output', 'methods'): p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args(); validate(a.analysis, a.figures, a.checks, a.unit_log, a.output, a.methods)
