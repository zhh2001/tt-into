"""预先选定主图、消融图及另列的事后密网格诊断图。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

from check_rolling_reference import load
from plot_rolling_reference import utilization
from analyze_rolling_reference import control_rate


LABELS = {'static': 'Tuned static TT-INT', 'joint': 'Uniform joint', 'phase_joint': 'Temporal joint',
          'uniform_guard': 'Uniform with repair', 'phase_guard': 'Temporal with repair',
          'phase_local_guard': 'Temporal local with repair', 'dense_static': 'Dense static TT-INT'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--density', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = load(args.run / 'manifest.json')
    rows, dense = load(args.run / 'summary.json'), load(args.density / 'summary.json')
    font_path = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(font_path))
    font = font_manager.FontProperties(fname=font_path, size=10)
    small = font_manager.FontProperties(fname=font_path, size=8)
    if font.get_name() != 'Times New Roman' or Path(font_manager.findfont(font, fallback_to_default=False)).resolve() != font_path.resolve():
        raise ValueError('字体解析失败')
    if Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'), fallback_to_default=False)).resolve() != font_path.resolve():
        raise ValueError('字体族静默回退')
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 9, 'pdf.fonttype': 42, 'axes.unicode_minus': False})
    args.output.mkdir(parents=True, exist_ok=False)
    colors, hatches = ['#e3e3e3', '#91afc6', '#d1b17c', '#a2ba99'], ['', '//', '..', 'xx']
    text_labels = ['Stable', 'Burst', 'Priority', 'Switch', 'Stop', 'Loss', 'Floor', 'Jitter', 'U. switch']
    tables = []

    def finish(fig, name, scope):
        fig.savefig(args.output / f'{name}.png', dpi=300, metadata={'Software': 'Matplotlib'})
        fig.savefig(args.output / f'{name}.pdf', metadata={'Author': '', 'Creator': '', 'Title': scope})
        plt.close(fig)
        fonts = subprocess.run(['pdffonts', str(args.output / f'{name}.pdf')], check=True, text=True, capture_output=True).stdout
        if not fonts.splitlines()[2:] or any('TimesNewRoman' not in line or ' yes ' not in line for line in fonts.splitlines()[2:]):
            raise ValueError('PDF 字体未按要求嵌入')
        (args.output / f'{name}-fonts.txt').write_text(fonts)

    selections = [('adaptive-loss-and-budget', ('static', 'joint', 'phase_guard', 'phase_local_guard')),
                  ('adaptive-ablations', ('joint', 'phase_joint', 'uniform_guard', 'phase_guard'))]
    for name, methods in selections:
        fig, axes = plt.subplots(2, 2, figsize=(7.8, 5.0), sharex=True)
        for column, level in enumerate(manifest['budgets']):
            for index, method in enumerate(methods):
                losses, utils, lranges, uranges, bads = [], [], [], [], []
                for scenario in manifest['scenarios']:
                    rs = [r for r in rows if r['scenario'] == scenario and r['budget_level'] == level and r['method'] == method]
                    if sorted(r['seed'] for r in rs) != sorted(manifest['test_seeds']):
                        raise ValueError('图中配对种子缺失')
                    ls = [r['loss'] for r in rs]
                    us = [utilization(r, manifest['budget_definition'][scenario][level]) for r in rs]
                    ml, mu = statistics.mean(ls), statistics.mean(us)
                    losses.append(ml)
                    utils.append(mu)
                    lranges.append((ml - min(ls), max(ls) - ml))
                    uranges.append((mu - min(us), max(us) - mu))
                    bads.append(sum(not r['feasible'] for r in rs))
                    tables.append({'figure': name, 'scenario': scenario, 'budget_level': level, 'method': method,
                                   'mean_loss': ml, 'min_loss': min(ls), 'max_loss': max(ls),
                                   'mean_utilization': mu, 'min_utilization': min(us), 'max_utilization': max(us),
                                   'infeasible_seeds': bads[-1]})
                x = np.arange(len(text_labels)) + (index - 1.5) * .18
                style = {'width': .18, 'color': colors[index], 'edgecolor': '#444444', 'linewidth': .4, 'hatch': hatches[index],
                         'error_kw': {'elinewidth': .55, 'capsize': 1.1, 'capthick': .5}}
                axes[0, column].bar(x, losses, yerr=np.asarray(lranges).T, label=LABELS[method], **style)
                axes[1, column].bar(x, utils, yerr=np.asarray(uranges).T, **style)
                for at, util, bad in zip(x, utils, bads):
                    if bad:
                        axes[1, column].plot(at, util, 'x', color='#8b2929', markersize=4, markeredgewidth=.8)
            axes[0, column].set_title('Low budget' if level == 'low' else 'High budget', fontproperties=font)
            axes[0, column].set_ylim(0, 4.3)
            axes[1, column].axhline(1, linestyle='--', color='#444444', linewidth=.6)
            axes[1, column].set_ylim(0, max(1.6, max(axis['max_utilization'] for axis in tables if axis['figure'] == name and axis['budget_level'] == level) * 1.1))
            axes[1, column].set_xticks(np.arange(len(text_labels)), text_labels, rotation=35, ha='right')
            for axis in axes[:, column]:
                axis.set_axisbelow(True)
                axis.grid(axis='y', color='#dedede', linewidth=.4)
                for tick in axis.get_xticklabels() + axis.get_yticklabels():
                    tick.set_fontproperties(small)
        axes[0, 0].set_ylabel('Clipped normalized age loss', fontproperties=font)
        axes[1, 0].set_ylabel('Maximum window budget utilization', fontproperties=font)
        handles, labels = axes[0, 0].get_legend_handles_labels()
        fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(.5, 1), ncol=2, prop=small, frameon=False)
        fig.text(.5, .025, 'Synthetic model, four new test seeds. Whiskers: observed range. Crosses: at least one infeasible seed.',
                 ha='center', fontproperties=small)
        fig.tight_layout(rect=(0, .09, 1, .91), pad=.55)
        finish(fig, name, name)

    fig, axes = plt.subplots(1, 3, figsize=(7.8, 2.8))
    diagnostic_tables = []
    for index, method in enumerate(('static', 'dense_static', 'phase_guard')):
        x = np.arange(2) + (index - 1) * .23
        values = [[], [], []]
        ranges = [[], [], []]
        for scenario in ('stable', 'shared_priority'):
            rs = [r for r in (dense if method == 'dense_static' else rows)
                  if r['scenario'] == scenario and r['budget_level'] == 'high' and (method == 'dense_static' or r['method'] == method)]
            columns = [[r['loss'] for r in rs], [r['cost']['export_attempt_bytes_per_s'] for r in rs], [control_rate(r) for r in rs]]
            diagnostic_tables.append({'scenario': scenario, 'method': method, 'mean_loss': statistics.mean(columns[0]),
                                      'mean_export_bytes_per_s': statistics.mean(columns[1]), 'mean_control_bytes_per_s': statistics.mean(columns[2])})
            for j, column in enumerate(columns):
                mean = statistics.mean(column)
                values[j].append(mean)
                ranges[j].append((mean - min(column), max(column) - mean))
        for j, axis in enumerate(axes):
            axis.bar(x, values[j], .23, yerr=np.asarray(ranges[j]).T, label=LABELS[method],
                     color=colors[index], edgecolor='#444444', linewidth=.4, hatch=hatches[index],
                     error_kw={'elinewidth': .55, 'capsize': 1.2, 'capthick': .5})
    for axis, title in zip(axes, ('Normalized age loss', 'Export attempts (B/s)', 'Control API (B/s)')):
        axis.set_title(title, fontproperties=font)
        axis.set_xticks([0, 1], ['Stable', 'Priority'])
        axis.grid(axis='y', color='#dedede', linewidth=.4)
        axis.set_axisbelow(True)
        for tick in axis.get_xticklabels() + axis.get_yticklabels():
            tick.set_fontproperties(small)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', ncol=3, prop=small, frameon=False)
    fig.text(.5, .045, 'Post hoc challenge, four already-used seeds. Dense static: eight thresholds. Controller: three thresholds.',
             ha='center', fontproperties=small)
    fig.tight_layout(rect=(0, .13, 1, .84), pad=.65)
    finish(fig, 'static-density-challenge', 'Post hoc static grid density challenge')
    for name, entries in [('plot-data.csv', tables), ('density-plot-data.csv', diagnostic_tables)]:
        with (args.output / name).open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(entries[0]), lineterminator='\n')
            writer.writeheader()
            writer.writerows(entries)
    sources = ('experiments/plot_adaptive_reference.py', 'experiments/plot_rolling_reference.py',
               'experiments/analyze_rolling_reference.py', 'experiments/check_rolling_reference.py')
    root = Path(__file__).resolve().parents[1]
    for source in sources:
        target = args.output / 'executed-source' / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / source, target)
    record = {'input_sha256': {kind: {name: hashlib.sha256((path / name).read_bytes()).hexdigest() for name in ('manifest.json', 'summary.json')}
                              for kind, path in (('primary', args.run), ('density', args.density))},
              'source_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources},
              'font_name': font.get_name(), 'font_file': str(font_path), 'font_sha256': hashlib.sha256(font_path.read_bytes()).hexdigest(),
              'dpi': 300, 'scope': '主图和消融图为预选方法；密网格图为事后不对称配置空间诊断；误差线均为观察范围',
              'artifact_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.iterdir() if p.is_file()}}
    (args.output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
