"""验收文献试点公开产物、源码快照、图源和后续矩阵绑定。"""
import argparse
import json
from pathlib import Path
import re
import subprocess

from PIL import Image
from check_rolling_reference import same
from literature_contract import ROOT, sha, validate
from prepare_literature_comparison import protocol, training_order


def check(data, figures, preparation, audit):
    load = lambda p: json.loads(p.read_text())
    manifest, checks = load(data / 'manifest.json'), 0
    validate(load(data / 'observer-plan.json'))
    same(sha(data / 'method-contract.json'), manifest['method_contract_sha256'], '实际方法合同绑定')
    for name, digest in manifest['artifact_sha256'].items():
        same(sha(data / name), digest, '完整公开数据摘要'); checks += 1
    for field, folder in (('source_sha256', 'executed-source'), ('analysis_source_sha256', 'executed-analysis-source')):
        for name, digest in manifest[field].items():
            same(sha(data / folder / name), digest, '实际执行/分析快照')
            same(sha(ROOT / name), digest, '冻结实际源码与当前工作区')
            checks += 2
    analysis = load(audit / 'analysis-checks.json')
    same(analysis['status'], 'passed', '独立实际费用与年龄分析验收')
    same(analysis['analysis_manifest_sha256'], sha(data / 'manifest.json'), '独立验收绑定')
    startup, final = (data / 'unit-tests.txt').read_text(), (audit / 'final-unit-tests.txt').read_text()
    if not all(re.search(r'Ran 486 tests.*\n\nOK', body, re.S) for body in (startup, final)):
        raise ValueError('启动和最终486项回归不符')
    chart = load(figures / 'manifest.json')
    for name, digest in chart['inputs_sha256'].items():
        same(sha(data / name), digest, '图输入绑定'); checks += 1
    same(sha(figures / 'plot-source.py'), chart['plot_source_sha256'], '归档图源')
    same(sha(ROOT / 'experiments/plot_literature_pilot.py'), chart['plot_source_sha256'], '当前图源')
    fonts = []
    for name, digest in chart['artifact_sha256'].items():
        path = figures / name
        same(sha(path), digest, '图文件摘要')
        if path.suffix == '.png':
            with Image.open(path) as im:
                if not all(abs(v - 300) < .05 for v in im.info['dpi']): raise ValueError('PNG分辨率不符')
        if path.suffix == '.pdf':
            result = subprocess.run(['pdffonts', str(path)], check=True, capture_output=True, text=True).stdout
            names = result.splitlines()[2:]
            if not names or any('TimesNewRoman' not in line or not re.search(r'yes\s+yes\s+yes', line) for line in names):
                raise ValueError('PDF未嵌入实际Times New Roman')
            fonts.append(dict(file=name, pdffonts=result))
        checks += 2
    matrix = load(preparation / 'protocol.json')
    same(matrix, protocol(), '预声明参数和有界矩阵')
    same(load(preparation / 'training-order.json'), training_order(matrix), '96例全部全局参数与顺序')
    prep = load(preparation / 'manifest.json')
    same(prep['fresh_inputs_generated'], False, '准备没有生成新业务')
    for name, digest in prep['artifact_sha256'].items():
        same(sha(preparation / name), digest, '矩阵准备摘要'); checks += 1
    checks += 11
    return dict(status='passed', delivery_checks=checks, data_manifest_sha256=sha(data / 'manifest.json'),
        analysis_checks_sha256=sha(audit / 'analysis-checks.json'), final_unit_tests_sha256=sha(audit / 'final-unit-tests.txt'),
        figures_manifest_sha256=sha(figures / 'manifest.json'), preparation_manifest_sha256=sha(preparation / 'manifest.json'),
        checker_sha256=sha(ROOT / 'experiments/check_literature_delivery.py'), embedded_fonts=fonts,
        execution_source_count=len(manifest['source_sha256']), analysis_source_count=len(manifest['analysis_source_sha256']))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('data', 'figures', 'preparation', 'audit'): p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    result = check(a.data, a.figures, a.preparation, a.audit)
    with (a.audit / 'delivery-checks.json').open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'embedded_fonts'}, ensure_ascii=False))
