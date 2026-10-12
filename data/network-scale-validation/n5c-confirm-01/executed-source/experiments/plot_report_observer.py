"""将真实共同入口试点绘成两位置年龄界与报告等待分布。"""
import argparse
import csv
from pathlib import Path
import shutil
import subprocess

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

from check_matched_network import load, sha
from analyze_report_observer import write

ROOT = Path(__file__).resolve().parents[1]


def plot(analysis, checks, output):
    manifest, audit = load(analysis / 'manifest.json'), load(checks)
    if audit['status'] != 'passed' or audit['cases'] != 8 or audit['analysis_manifest_sha256'] != sha(analysis / 'manifest.json'):
        raise ValueError('绘图需要绑定实际八案例的独立验收')
    for name in ('summary.csv', 'report-availability.csv'):
        if sha(analysis / name) != manifest['artifact_sha256'][name]: raise ValueError('绘图数据摘要改变')
    with (analysis / 'summary.csv').open() as stream: summaries = list(csv.DictReader(stream))
    with (analysis / 'report-availability.csv').open() as stream: available = list(csv.DictReader(stream))
    fp = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(fp)); font = font_manager.FontProperties(fname=fp)
    if font.get_name() != 'Times New Roman' or Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'), fallback_to_default=False)).resolve() != fp.resolve():
        raise ValueError('Times New Roman字体解析失败')
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 10, 'pdf.fonttype': 42, 'axes.unicode_minus': False})
    output.mkdir(parents=True, exist_ok=False)

    def finish(fig, name, title):
        fig.savefig(output / (name + '.png'), dpi=300, metadata={'Software': 'Matplotlib'})
        fig.savefig(output / (name + '.pdf'), metadata={'Author': '', 'Creator': '', 'Title': title})
        plt.close(fig)
        fonts = subprocess.check_output(['pdffonts', str(output / (name + '.pdf'))], text=True)
        font_rows = fonts.splitlines()[2:]
        if not font_rows or any('TimesNewRoman' not in r or ' yes ' not in r for r in font_rows):
            raise ValueError('PDF没有正确嵌入Times New Roman')
        (output / (name + '-fonts.txt')).write_text(fonts)

    profiles = ('stable', 'shared_priority', 'switch', 'bursty_jitter')
    labels = ('Stable', 'Priority', 'Switch', 'Bursty jitter')
    colors = {'static': '#286f95', 'phase_guard': '#b46b30'}
    fig, axes = plt.subplots(1, 2, figsize=(8.4, 3.7), sharey=True)
    for axis, observer in zip(axes, ('collector', 'controller')):
        for i, scenario in enumerate(profiles):
            for j, method in enumerate(('static', 'phase_guard')):
                r = next(r for r in summaries if r['scenario'] == scenario and r['method'] == method)
                lo, hi = float(r[f'{observer}_loss_lower']), float(r[f'{observer}_loss_upper']); mid = (lo + hi) / 2
                x = i + (j - .5) * .30
                axis.bar(x, mid, width=.27, color=colors[method], label=('Static TT-INT' if j == 0 else 'Phase guard P02') if i == 0 else None)
                axis.errorbar(x, mid, yerr=[[mid-lo], [hi-mid]], fmt='none', color='#222222', capsize=3, linewidth=.7)
                if r['window_feasibility'] != 'within': axis.text(x, hi + .03, '!', ha='center')
        axis.set_xticks(range(4), labels, rotation=20, ha='right')
        axis.set_title(observer.title() + ' age loss'); axis.grid(axis='y', linewidth=.4, color='#dddddd'); axis.set_axisbelow(True)
    axes[0].set_ylabel('Weighted normalized age loss'); axes[-1].legend(frameon=False, fontsize=8)
    fig.text(.5, .017, 'Consumed training seed 11, one deployment per cell. Conditional bounds, not confidence intervals.', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .06, 1, 1)); finish(fig, 'pilot-observer-age', 'Common report observer correctness pilot')

    fig, axis = plt.subplots(figsize=(6.5, 3.7))
    for method in ('static', 'phase_guard'):
        values = sorted(float(r['receipt_to_entry_upper_ms']) for r in available if r['method'] == method)
        axis.step(values, [(i + 1) / len(values) for i in range(len(values))], where='post', color=colors[method],
                  label=('Static TT-INT' if method == 'static' else 'Phase guard P02') + f' (n={len(values)})')
    axis.axvline(100, linestyle=':', linewidth=.8, color='#666666', label='Scheduled observation period')
    axis.set_xlabel('recvmsg completion to cache update upper bound (ms)'); axis.set_ylabel('Empirical cumulative fraction')
    axis.set_ylim(0, 1.04); axis.set_xlim(0, max(float(r['receipt_to_entry_upper_ms']) for r in available) + 3)
    axis.legend(frameon=False, fontsize=8, loc='upper left'); axis.grid(linewidth=.4, color='#dddddd')
    tail = manifest['findings']['reports'] - len(available)
    fig.text(.5, .02, f'{len(available)} first visible reports; {tail} unread tail reports retained in age evaluation. No hard delay guarantee.', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .06, 1, 1)); finish(fig, 'pilot-report-wait', 'Report observation delay in the common entry pilot')
    source = 'experiments/plot_report_observer.py'
    target = output / 'executed-source' / source; target.parent.mkdir(parents=True); shutil.copyfile(ROOT / source, target)
    write(output / 'manifest.json', {'status': 'passed', 'analysis_manifest_sha256': sha(analysis / 'manifest.json'),
        'independent_checks_sha256': sha(checks), 'font_name': font.get_name(), 'font_file': str(fp), 'font_sha256': sha(fp),
        'source_sha256': {source: sha(ROOT / source)},
        'artifact_sha256': {str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('analysis', 'checks', 'output'): p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args(); plot(a.analysis, a.checks, a.output)
