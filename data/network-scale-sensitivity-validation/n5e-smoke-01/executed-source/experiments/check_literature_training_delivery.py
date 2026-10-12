"""绑定训练、校准、全局参数、正式图和未生成业务的比较准备。"""
import argparse
import json
from pathlib import Path
import re
import subprocess

from PIL import Image
from check_rolling_reference import same
from literature_stage_contract import ROOT, sha, validate


def check(data_paths, selections, figure_paths, audit, comparison):
    load = lambda p: json.loads(p.read_text())
    count, summaries = 0, []
    for data, selection, figures in zip(data_paths, selections, figure_paths):
        m, sm, fm = [load(p / 'manifest.json') for p in (data, selection, figures)]
        stage = m['stage']
        validate(load(data / 'stage-plan.json'))
        analysis = load(audit / (stage + '-analysis-checks.json'))
        chosen = load(audit / (stage + '-selection-checks.json'))
        same(analysis['status'], 'passed', '完整分析独立核对')
        same(analysis['analysis_manifest_sha256'], sha(data / 'manifest.json'), '分析输入绑定')
        same(chosen['selection_manifest_sha256'], sha(selection / 'manifest.json'), '全局排序绑定')
        same(chosen['status'], 'passed', '独立全局选择')
        same(sm['analysis_manifest_sha256'], sha(data / 'manifest.json'), '冻结参数绑定全部分析')
        for name, digest in m['artifact_sha256'].items(): same(sha(data / name), digest, '全部公开测量'); count += 1
        for field, folder in (('source_sha256', 'executed-source'), ('analysis_source_sha256', 'executed-analysis-source')):
            for name, digest in m[field].items():
                same(sha(data / folder / name), digest, '实际源码归档')
                same(sha(ROOT / name), digest, '实际版本与当前源码'); count += 2
        for name, digest in sm['artifact_sha256'].items(): same(sha(selection / name), digest, '选参附件'); count += 1
        same(fm['data_manifest_sha256'], sha(data / 'manifest.json'), '图完整数据绑定')
        same(fm['summary_sha256'], sha(data / 'summary.csv'), '图实际费用绑定')
        same(fm['selection_manifest_sha256'], sha(selection / 'manifest.json'), '图全局评分绑定')
        same(fm['selection_sha256'], sha(selection / 'selection.json'), '图全局选择绑定')
        same(sha(figures / 'plot-source.py'), fm['plot_source_sha256'], '实际图源')
        same(sha(ROOT / 'experiments/plot_literature_training.py'), fm['plot_source_sha256'], '当前图源')
        fonts = []
        for name, digest in fm['artifact_sha256'].items():
            path = figures / name; same(sha(path), digest, '图输出摘要')
            if path.suffix == '.png':
                with Image.open(path) as im:
                    if not all(abs(x - 300) < .05 for x in im.info['dpi']): raise ValueError('PNG非300dpi')
            if path.suffix == '.pdf':
                result = subprocess.run(['pdffonts', str(path)], capture_output=True, text=True, check=True).stdout
                lines = result.splitlines()[2:]
                if not lines or any('TimesNewRoman' not in line or not re.search(r'yes\s+yes\s+yes', line) for line in lines):
                    raise ValueError('PDF字体未正确嵌入')
                fonts.append(dict(file=name, pdffonts=result))
            count += 2
        if not re.search(r'Ran 498 tests.*\n\nOK', (data / 'unit-tests.txt').read_text(), re.S): raise ValueError('启动回归不符')
        summaries.append(dict(stage=stage, findings=load(data / 'findings.json'),
            selected_indices=load(selection / 'selection.json')['selected_indices'], embedded_fonts=fonts))
        count += 12
    if not re.search(r'Ran 498 tests.*\n\nOK', (audit / 'final-unit-tests.txt').read_text(), re.S): raise ValueError('最终回归不符')
    plan, manifest = load(comparison / 'stage-plan.json'), load(comparison / 'manifest.json')
    validate(plan, current=True)
    same(plan['stage'], 'comparison', '比较入口')
    same(plan['expected_cases'], 320, '十方法完整矩阵')
    same(manifest['fresh_inputs_generated'], False, '尚未生成新测试')
    same(plan['selection'], load(selections[1] / 'selection.json'), '最终参数接入全部比较')
    for name, digest in manifest['artifact_sha256'].items(): same(sha(comparison / name), digest, '首例前比较附件'); count += 1
    return dict(status='passed', delivery_checks=count + 4, stages=summaries,
        comparison_plan_sha256=sha(comparison / 'stage-plan.json'), fresh_business_seeds=plan['business_seeds'],
        fresh_inputs_generated=False, final_unit_tests_sha256=sha(audit / 'final-unit-tests.txt'),
        checker_sha256=sha(ROOT / 'experiments/check_literature_training_delivery.py'))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('training', 'calibration', 'training-selection', 'calibration-selection',
                 'training-figures', 'calibration-figures', 'audit', 'comparison'):
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    result = check([a.training, a.calibration], [a.training_selection, a.calibration_selection],
                   [a.training_figures, a.calibration_figures], a.audit, a.comparison)
    with (a.audit / 'delivery-checks.json').open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'stages'}, ensure_ascii=False))
