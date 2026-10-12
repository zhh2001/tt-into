"""完整全局参数评分及校准选中配置的双观察位置图。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

METHODS = ('static', 'feedback', 'local', 'joint', 'phase_joint', 'uniform_guard', 'phase_guard', 'phase_local_guard')
LABELS = ('Static', 'Feedback', 'Local', 'Joint', 'Phase joint', 'Uniform guard', 'Phase guard', 'Phase local')
PROFILES = (('stable', 'high', 'Stable / high'), ('shared_priority', 'high', 'Priority / high'),
            ('switch', 'low', 'Notified switch / low'), ('bursty_jitter', 'low', 'Jitter / low'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plot(analysis, output):
    manifest = json.loads((analysis / 'manifest.json').read_text())
    if manifest['status'] != 'passed' or len(manifest['cases']) != 184:
        raise ValueError('图源必须为完整有限动态校准')
    names = ('parameter-scores.json', 'frozen-parameters.json', 'selected-calibration-cases.json',
             'summary.csv', 'case-availability.csv', 'summary-checks.json')
    for name in names:
        if sha(analysis / name) != manifest['artifact_sha256'][name]:
            raise ValueError('图源摘要不同')
    if json.loads((analysis / 'summary-checks.json').read_text())['status'] != 'passed':
        raise ValueError('图源未独立通过')
    scores = json.loads((analysis / 'parameter-scores.json').read_text())
    frozen = json.loads((analysis / 'frozen-parameters.json').read_text())
    selected = json.loads((analysis / 'selected-calibration-cases.json').read_text())
    with (analysis / 'summary.csv').open() as stream:
        summaries = {r['case']: r for r in csv.DictReader(stream)}
    with (analysis / 'case-availability.csv').open() as stream:
        views = {r['name']: r for r in csv.DictReader(stream)}
    fp = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(fp))
    font = font_manager.FontProperties(fname=fp)
    resolved = Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'), fallback_to_default=False))
    if font.get_name() != 'Times New Roman' or resolved.resolve() != fp.resolve():
        raise ValueError('Times New Roman 解析失败')
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 9, 'pdf.fonttype': 42,
                         'axes.unicode_minus': False})
    output.mkdir(parents=True, exist_ok=False)

    def finish(fig, name, title):
        fig.savefig(output / (name + '.png'), dpi=300, metadata={'Software': 'Matplotlib'})
        fig.savefig(output / (name + '.pdf'), metadata={'Author': '', 'Creator': '', 'Title': title})
        plt.close(fig)
        fonts = subprocess.run(['pdffonts', str(output / (name + '.pdf'))], capture_output=True, text=True, check=True).stdout
        if not fonts.splitlines()[2:] or any('TimesNewRoman' not in line or ' yes ' not in line for line in fonts.splitlines()[2:]):
            raise ValueError('PDF 字体嵌入错误')
        (output / (name + '-fonts.txt')).write_text(fonts)

    fig, axes = plt.subplots(1, 3, figsize=(10, 4), sharey=True)
    for axis, method, label in zip(axes, METHODS[-3:], LABELS[-3:]):
        settings = [r for r in scores if r['method'] == method]
        if len(settings) != 6: raise ValueError('全局参数图分母错误')
        winner = next(r for r in frozen if r['method'] == method)
        for r in settings:
            i = r['parameter_index']
            if not r['evaluable']:
                axis.text(i, 4, 'Unavailable', rotation=90, ha='center', fontsize=7)
                continue
            bounds = [[float(summaries[n]['loss_' + side]) for side in ('lower', 'upper')] for n in r['case_names']]
            lo, hi = sum(b[0] for b in bounds) / 8, sum(b[1] for b in bounds) / 8
            mid = (lo + hi) / 2
            axis.errorbar(i, mid, yerr=[[mid - lo], [hi - mid]], fmt='o' if r['feasible'] else 'x',
                          color='#27749c' if r['feasible'] else '#b32d2d', markersize=5, capsize=3)
            axis.annotate(f"{r['rank'][0]}/8", (i, mid), xytext=(0, 9), textcoords='offset points', ha='center', fontsize=8)
            if i == winner['selected_parameter']:
                axis.plot(i, mid, 's', color='#333333', fillstyle='none', markersize=10)
        axis.set_title(label)
        axis.set_xticks(range(6))
        axis.set_xlim(-.5, 5.5)
        axis.set_xlabel('Frozen parameter index')
        axis.grid(color='#dddddd', linewidth=.4)
        axis.set_axisbelow(True)
    axes[0].set_ylabel('Mean collector age loss')
    handles = [plt.Line2D([], [], marker=m, color=c, linestyle='None', fillstyle=fill, label=label)
               for m, c, fill, label in [('o', '#27749c', 'full', 'All eight cases within budget'),
                                         ('x', '#b32d2d', 'full', 'At least one budget failure'),
                                         ('s', '#333333', 'none', 'Global parameter selected')]]
    fig.legend(handles=handles, loc='upper center', ncol=3, frameon=False, fontsize=8)
    fig.text(.5, .015, 'All 18 parameter scores. Labels show non-within cases. Conditional intervals, not confidence intervals.', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .06, 1, .90), pad=1)
    finish(fig, 'dynamic-global-parameter-calibration', 'Finite global network parameter calibration with all budget failures')

    fig, axes = plt.subplots(2, 4, figsize=(12, 6.5), sharey=True)
    for col, (s, b, label) in enumerate(PROFILES):
        for row, observer in enumerate(('collector', 'controller')):
            axis = axes[row, col]
            for i, method in enumerate(METHODS):
                rs = [r for r in selected if (r['scenario'], r['budget_level'], r['method']) == (s, b, method)]
                if not rs:
                    axis.text(i, 4, 'Unavailable', rotation=90, ha='center', fontsize=7)
                    continue
                if len(rs) != 2 or {r['seed'] for r in rs} != {1301, 1423}:
                    raise ValueError('选中全局参数的双业务展示分母错误')
                source = rs if observer == 'collector' else [views[r['case']] for r in rs]
                prefix = '' if observer == 'collector' else 'controller_'
                lo = sum(float(r[prefix + 'loss_lower']) for r in source) / 2
                hi = sum(float(r[prefix + 'loss_upper']) for r in source) / 2
                mid = (lo + hi) / 2
                color = '#333333' if method == 'static' else '#27749c'
                axis.errorbar(i, mid, yerr=[[mid - lo], [hi - mid]], fmt='D' if method == 'static' else 'o',
                              color=color, markersize=4, capsize=3)
                if any(r['window_feasibility'] != 'within' for r in rs):
                    axis.plot(i, mid, 'x', color='#b32d2d', markersize=8)
            axis.set_title(label + (' / collector' if row == 0 else ' / controller'))
            axis.set_xticks(range(8), LABELS, rotation=55, ha='right', fontsize=8)
            axis.set_ylim(0, 4.2)
            axis.set_xlim(-.5, 7.5)
            axis.grid(axis='y', color='#dddddd', linewidth=.4)
            axis.set_axisbelow(True)
            if col == 0: axis.set_ylabel('Clipped normalized age loss')
    fig.text(.5, .015, 'Calibration data after global selection. Two business seeds. Conditional means, not confidence intervals. Red crosses mark budget failures.', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .065, 1, 1), pad=1)
    finish(fig, 'dynamic-selected-calibration-observers', 'Collector and controller age for all methods at selected calibration parameters')
    for name in names:
        shutil.copyfile(analysis / name, output / name)
    source = Path(__file__).resolve()
    target = output / 'executed-source/experiments' / source.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    record = dict(status='passed', analysis_manifest_sha256=sha(analysis / 'manifest.json'),
        input_sha256={n: sha(analysis / n) for n in names}, source_sha256={'experiments/' + source.name: sha(source)},
        font_name=font.get_name(), font_file=str(fp), font_sha256=sha(fp), dpi=300,
        scope='有限网络参数校准，全部失败保留，两观察位置分列；不是独立测试排名',
        artifact_sha256={str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()})
    (output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plot(args.analysis, args.output)
