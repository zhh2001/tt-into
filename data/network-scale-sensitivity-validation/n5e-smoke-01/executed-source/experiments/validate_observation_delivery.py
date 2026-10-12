"""绑定观察诊断独立验收、执行源码、完整回归和图片字体。"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess

from PIL import Image
from observation_diagnostics import INPUT_SHA256, load, sha, write

ROOT = Path(__file__).resolve().parents[1]


def validate(diagnostic, figures, checks, unit_log, output):
    m, f, audit = load(diagnostic / 'manifest.json'), load(figures / 'manifest.json'), load(checks)
    if audit['status'] != 'passed' or audit['diagnostic_manifest_sha256'] != sha(diagnostic / 'manifest.json') or audit['input_manifest_sha256'] != INPUT_SHA256:
        raise ValueError('独立验收未绑定本诊断')
    if f['diagnostic_manifest_sha256'] != sha(diagnostic / 'manifest.json') or f['independent_checks_sha256'] != sha(checks):
        raise ValueError('绘图未绑定已验收诊断')
    count = 0
    source_checks = {}
    for record, directory in ((m, diagnostic), (f, figures)):
        for name, digest in record['artifact_sha256'].items():
            if sha(directory / name) != digest:
                raise ValueError('交付产物摘要改变')
            count += 1
        for name, digest in record['source_sha256'].items():
            if sha(ROOT / name) != digest or sha(directory / 'executed-source' / name) != digest:
                raise ValueError('实际源码、存档与待交付源码不同')
            source_checks[name] = digest
            count += 2
    log = unit_log.read_text()
    match = re.search(r'Ran (\d+) tests in [\d.]+s\s+OK\s*$', log)
    if not match or int(match.group(1)) < 362:
        raise ValueError('完整回归未通过或日志不完整')
    fonts, images = {}, {}
    for name in ('priority-observation-loss', 'priority-task-contributions', 'report-batch-outcomes'):
        pdf = figures / (name + '.pdf')
        actual = subprocess.check_output(['pdffonts', str(pdf)], text=True)
        if actual != (figures / (name + '-fonts.txt')).read_text():
            raise ValueError('字体证据与PDF不一致')
        font_rows = actual.splitlines()[2:]
        if not font_rows or any('TimesNewRoman' not in line or ' yes ' not in line for line in font_rows):
            raise ValueError('PDF并非嵌入真正Times New Roman')
        with Image.open(figures / (name + '.png')) as im:
            dpi = im.info.get('dpi')
            if dpi is None or any(abs(value - 300) > .01 for value in dpi):
                raise ValueError('PNG并非300dpi')
            images[name] = {'pixels': list(im.size), 'dpi': list(dpi)}
        fonts[name] = font_rows
        count += 4
    fp = Path(f['font_file'])
    if f['font_name'] != 'Times New Roman' or sha(fp) != f['font_sha256']:
        raise ValueError('字体文件摘要不符')
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(checks, output / 'independent-checks.json')
    shutil.copyfile(unit_log, output / 'unit-tests.txt')
    write(output / 'source-consistency.json', source_checks)
    results = {'status': 'passed', 'checks': count, 'independent_checks': audit['checks'],
               'unit_tests': int(match.group(1)), 'input_manifest_sha256': INPUT_SHA256,
               'diagnostic_manifest_sha256': sha(diagnostic / 'manifest.json'),
               'figure_manifest_sha256': sha(figures / 'manifest.json'), 'images': images, 'embedded_fonts': fonts,
               'scope': '来源、源码、完整回归和导出验证；独立年龄与观察核对见绑定检查文件'}
    write(output / 'delivery-checks.json', results)
    names = ('experiments/validate_observation_delivery.py', 'experiments/observation_diagnostics.py')
    for name in names:
        target = output / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT / name, target)
    write(output / 'manifest.json', {'status': 'passed', 'input_manifest_sha256': INPUT_SHA256,
          'diagnostic_manifest_sha256': sha(diagnostic / 'manifest.json'), 'figure_manifest_sha256': sha(figures / 'manifest.json'),
          'findings': results, 'source_sha256': {n: sha(ROOT / n) for n in names},
          'artifact_sha256': {str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}})
    print(__import__('json').dumps({'status': 'passed', 'checks': count, 'independent_checks': audit['checks'], 'unit_tests': int(match.group(1))}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('diagnostic', 'figures', 'checks', 'unit-log', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    a = parser.parse_args()
    validate(a.diagnostic, a.figures, a.checks, a.unit_log, a.output)
