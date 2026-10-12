"""绘制观察损失分解、任务贡献和完整报告处理去向。"""
import argparse
import csv
from pathlib import Path
import shutil
import subprocess

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

from observation_diagnostics import load, sha, write
from plot_network_comparison import METHODS, LABELS, PROFILES


def plot(diagnostic, output, checks):
    manifest = load(diagnostic / 'manifest.json')
    audit = load(checks)
    if manifest['status'] != 'passed' or audit['status'] != 'passed' or audit['cases'] != 256:
        raise ValueError('绘图要求完整且已独立通过的观察诊断')
    if audit['diagnostic_manifest_sha256'] != sha(diagnostic / 'manifest.json'):
        raise ValueError('独立验收记录不属于本诊断')
    for name in ('paired-observers.json', 'task-losses.csv', 'case-diagnostics.csv', 'method-diagnostics.csv', 'findings.json'):
        if sha(diagnostic / name) != manifest['artifact_sha256'][name]:
            raise ValueError('观察图源摘要不符')
    pairs = load(diagnostic / 'paired-observers.json')
    primary = sorted([p for p in pairs if p['scenario'] == 'shared_priority' and p['method'] == 'phase_guard'], key=lambda p: (p['seed'], p['block']))
    if len(primary) != 8:
        raise ValueError('重点配对必须包含四业务两块')
    with (diagnostic / 'task-losses.csv').open() as stream: tasks = list(csv.DictReader(stream))
    with (diagnostic / 'method-diagnostics.csv').open() as stream: aggregate = list(csv.DictReader(stream))
    fp = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(fp))
    font = font_manager.FontProperties(fname=fp)
    if font.get_name() != 'Times New Roman' or Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'), fallback_to_default=False)).resolve() != fp.resolve():
        raise ValueError('Times New Roman字体解析失败')
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 9, 'pdf.fonttype': 42, 'axes.unicode_minus': False})
    output.mkdir(parents=True, exist_ok=False)

    def finish(fig, name, title):
        fig.savefig(output / (name + '.png'), dpi=300, metadata={'Software': 'Matplotlib'})
        fig.savefig(output / (name + '.pdf'), metadata={'Author': '', 'Creator': '', 'Title': title})
        plt.close(fig)
        fonts = subprocess.check_output(['pdffonts', str(output / (name + '.pdf'))], text=True)
        if not fonts.splitlines()[2:] or any('TimesNewRoman' not in line or ' yes ' not in line for line in fonts.splitlines()[2:]):
            raise ValueError('PDF字体没有正确嵌入')
        (output / (name + '-fonts.txt')).write_text(fonts)

    colors = ('#276c8e', '#a6672c', '#513d75')
    fig, axis = plt.subplots(figsize=(8, 3.5))
    for j, field in enumerate(('collector_difference', 'penalty_difference', 'controller_difference')):
        for i, p in enumerate(primary):
            lo, hi = p[field]['lower'], p[field]['upper']; mid = (lo + hi) / 2
            x = i + (j - 1) * .23
            axis.bar(x, mid, width=.20, color=colors[j], label=('Collector', 'Added observation loss', 'Controller')[j] if i == 0 else None)
            axis.errorbar(x, mid, yerr=[[mid - lo], [hi - mid]], color='#222222', capsize=2, linewidth=.65, fmt='none')
    axis.axhline(0, color='#777777', linewidth=.7)
    axis.set_xticks(range(8), [f"{p['seed']} / {p['block']}" for p in primary])
    axis.set_xlabel('Business seed / deployment block')
    axis.set_ylabel('Age-loss difference from static')
    axis.set_title('Priority tasks: phase guard versus static')
    axis.legend(frameon=False, ncol=3, loc='upper left', fontsize=8)
    axis.grid(axis='y', linewidth=.4, color='#dddddd'); axis.set_axisbelow(True)
    fig.text(.5, .018, 'Conditional bounds, not confidence intervals. Added loss is an observer difference, not a causal intervention.', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .055, 1, 1))
    finish(fig, 'priority-observation-loss', 'Priority task observation loss decomposition')

    fig, axes = plt.subplots(1, 3, figsize=(9, 3.4), sharey=True)
    for axis, node in zip(axes, (1, 2, 3)):
        for j, observer in enumerate(('collector', 'controller')):
            for i, p in enumerate(primary):
                t = next(t for t in tasks if t['name'] == p['dynamic_case'] and int(t['node']) == node)
                s = next(t2 for t2 in tasks if t2['name'] == p['static_case'] and int(t2['node']) == node)
                fraction = float(t['denominator_fraction'])
                lo = fraction * (float(t[observer + '_loss_lower']) - float(s[observer + '_loss_upper']))
                hi = fraction * (float(t[observer + '_loss_upper']) - float(s[observer + '_loss_lower']))
                mid = (lo + hi) / 2; x = i + (j - .5) * .21
                axis.errorbar(x, mid, yerr=[[mid-lo], [hi-mid]], fmt='o' if j == 0 else 's', color=colors[j*2],
                              capsize=2, linewidth=.6, markersize=3, label=observer.title() if i == 0 else None)
        axis.axhline(0, color='#777777', linewidth=.7)
        axis.set_title(f'Node {node}, weight ' + ('4' if node == 3 else '1'))
        axis.set_xticks(range(8), [f"{p['seed']} / {p['block']}" for p in primary], rotation=55, ha='right', fontsize=7)
        axis.grid(axis='y', linewidth=.4, color='#dddddd'); axis.set_axisbelow(True)
    axes[0].set_ylabel('Weighted task contribution to difference')
    axes[-1].legend(frameon=False, fontsize=8)
    fig.text(.5, .015, 'All eight pairs retained. Weights use the complete active-task denominator; intervals are conditional bounds.', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .065, 1, 1))
    finish(fig, 'priority-task-contributions', 'Priority task contributions at two observation positions')

    fig, axes = plt.subplots(2, 2, figsize=(9, 6), sharey=True)
    fields = ('batch_latest_records_mean', 'superseded_in_batch_records_mean', 'tail_not_read_records_mean')
    labels = ('Latest after batch', 'Replaced within batch', 'Not read in cycles')
    for axis, (scenario, level, title) in zip(axes.flat, PROFILES):
        members = [next(r for r in aggregate if r['scenario'] == scenario and r['budget_level'] == level and r['method'] == method) for method in METHODS]
        bottom = [0.] * len(METHODS)
        for field, label, color in zip(fields, labels, ('#286c73', '#91b4bd', '#c9b895')):
            values = [float(r[field]) for r in members]
            axis.bar(range(8), values, bottom=bottom, color=color, width=.72, label=label)
            bottom = [x+y for x,y in zip(bottom,values)]
        axis.set_title(title)
        axis.set_xticks(range(8), LABELS, rotation=45, ha='right', fontsize=7)
        axis.set_ylabel('Mean report records per case')
        axis.grid(axis='y', linewidth=.4, color='#dddddd'); axis.set_axisbelow(True)
    handles, labels = axes[0,0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', ncol=3, frameon=False, fontsize=8)
    fig.text(.5, .015, 'Four businesses, two deployments each. Batch replacement describes visibility, not total information value.', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .06, 1, .94))
    finish(fig, 'report-batch-outcomes', 'Report visibility and batch replacement')

    source = Path(__file__).resolve()
    target = output / 'executed-source' / 'experiments' / source.name
    target.parent.mkdir(parents=True); shutil.copyfile(source, target)
    for dependency in ('observation_diagnostics.py', 'plot_network_comparison.py'):
        shutil.copyfile(source.parent / dependency, target.parent / dependency)
    shutil.copyfile(checks, output / 'independent-checks.json')
    write(output / 'manifest.json', {'status': 'passed', 'diagnostic_manifest_sha256': sha(diagnostic / 'manifest.json'),
          'independent_checks_sha256': sha(checks), 'font_name': font.get_name(), 'font_file': str(fp),
          'font_sha256': sha(fp), 'dpi': 300, 'source_sha256': {'experiments/' + n: sha(source.parent / n)
          for n in (source.name, 'observation_diagnostics.py', 'plot_network_comparison.py')},
          'artifact_sha256': {str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostic', type=Path, required=True)
    parser.add_argument('--checks', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plot(args.diagnostic, args.output, args.checks)
