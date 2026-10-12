"""绑定控制器目标静态训练、独立排序、完整回归和TNR导出。"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess

from PIL import Image
from analyze_observer_training import write
from check_matched_network import load, sha

ROOT = Path(__file__).resolve().parents[1]


def validate(analysis, figures, checks, unit_log, diagnostics, output):
    m, f, audit = load(analysis / 'manifest.json'), load(figures / 'manifest.json'), load(checks)
    d = load(diagnostics / 'manifest.json')
    if audit['status'] != 'passed' or audit['cases'] != 192 or audit['analysis_manifest_sha256'] != sha(analysis / 'manifest.json'):
        raise ValueError('独立验收没有绑定实际试点')
    if f['analysis_manifest_sha256'] != sha(analysis / 'manifest.json') or f['independent_checks_sha256'] != sha(checks):
        raise ValueError('图片没有绑定实际分析验收')
    if (d['status'] != 'passed' or d['analysis_manifest_sha256'] != sha(analysis / 'manifest.json')
            or d['independent_checks_sha256'] != sha(checks) or d['findings']['pairs'] != 8):
        raise ValueError('完整双业务诊断没有绑定实际分析验收')
    count = 0
    for directory, record in ((analysis, m), (figures, f), (diagnostics, d)):
        for name, digest in record['artifact_sha256'].items():
            if sha(directory / name) != digest: raise ValueError('产物摘要改变')
            count += 1
        for key, folder in (('source_sha256', 'executed-source'), ('analysis_source_sha256', 'executed-analysis-source')):
            for name, digest in record.get(key, {}).items():
                if sha(ROOT / name) != digest or sha(directory / folder / name) != digest: raise ValueError('源码、实际快照和交付不一致')
                count += 2
    log = unit_log.read_text(); match = re.search(r'Ran (\d+) tests in [\d.]+s\s+OK\s*$', log)
    if not match or int(match.group(1)) < 410: raise ValueError('最终完整回归缺失')
    images, fonts = {}, {}
    for name in ('finite-static-cost-challenge', 'controller-training-selection'):
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
    result = {'status': 'passed', 'cases': 192, 'unit_tests': int(match.group(1)), 'delivery_checks': count,
        'network_checks': audit['network_checks'], 'analysis_checks': audit['analysis_checks'], 'selection_checks': audit['selection_checks'],
        'analysis_manifest_sha256': sha(analysis / 'manifest.json'), 'figure_manifest_sha256': sha(figures / 'manifest.json'),
        'diagnostics_manifest_sha256': sha(diagnostics / 'manifest.json'),
        'images': images, 'embedded_fonts': fonts, 'scope': '24/6561有限静态训练及六层费用挑战验收；不是全网格最优、新测试排名或已冻结共同初始化'}
    write(output / 'delivery-checks.json', result)
    source = 'experiments/validate_observer_training_delivery.py'; target = output / 'executed-source' / source
    target.parent.mkdir(parents=True); shutil.copyfile(ROOT / source, target)
    write(output / 'manifest.json', dict(status='passed', findings=result, source_sha256={source: sha(ROOT / source)},
        artifact_sha256={str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}))
    print(__import__('json').dumps({k: result[k] for k in ('status', 'cases', 'unit_tests', 'delivery_checks', 'network_checks', 'analysis_checks', 'selection_checks')}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('analysis', 'figures', 'checks', 'unit-log', 'diagnostics', 'output'): p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args(); validate(a.analysis, a.figures, a.checks, a.unit_log, a.diagnostics, a.output)
