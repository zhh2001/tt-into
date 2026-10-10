"""保留种子的完整损失与实际费用图，字体和嵌入结果显式核对。"""
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


def utilization(row, budget):
    rates = row['cost']['inband_per_link_bytes_per_s']
    seconds = (row['cost']['window_us'][1] - row['cost']['window_us'][0]) / 1e6
    values = [rates.get(link, 0) / cap for link, cap in budget['link_bytes_per_s'].items()]
    values += [row['cost']['export_attempt_bytes_per_s'] / budget['export_bytes_per_s'],
               sum(row['cost']['control_serialized_bytes_by_kind'].values()) / seconds / budget['control_bytes_per_s']]
    return max(values)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = load(args.run / 'manifest.json')
    rows = load(args.run / 'summary.json')
    font_path = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(font_path))
    font = font_manager.FontProperties(fname=font_path, size=10)
    small = font_manager.FontProperties(fname=font_path, size=8)
    resolved = Path(font_manager.findfont(font, fallback_to_default=False))
    family_resolved = Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'), fallback_to_default=False))
    if resolved.resolve() != font_path.resolve() or family_resolved.resolve() != font_path.resolve() or font.get_name() != 'Times New Roman':
        raise ValueError('字体未实际解析为 Times New Roman')
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 9, 'pdf.fonttype': 42, 'axes.unicode_minus': False})
    args.output.mkdir(parents=True, exist_ok=False)
    figure, axes = plt.subplots(2, 2, figsize=(7.4, 4.7), sharex=True)
    names = {'static': 'Tuned static TT-INT', 'feedback': 'Common feedback',
             'local': 'Local scoring', 'joint': 'Joint scoring'}
    colors, hatches = ['#e5e5e5', '#86a3be', '#dfb46e', '#aabf9c'], ['', '//', '..', 'xx']
    labels = ['Stable', 'Burst', 'Priority', 'Switch', 'Stop', 'Loss', 'Floor']
    stats = []
    width = .18
    for column, level in enumerate(manifest['budgets']):
        for i, method in enumerate(manifest['methods']):
            losses, utils, loss_range, util_range, failures = [], [], [], [], []
            for scenario in manifest['scenarios']:
                entries = [r for r in rows if r['scenario'] == scenario and r['budget_level'] == level and r['method'] == method]
                if sorted(r['seed'] for r in entries) != sorted(manifest['test_seeds']):
                    raise ValueError('配对种子缺失或重复')
                ls = [r['loss'] for r in entries]
                us = [utilization(r, manifest['budget_definition'][scenario][level]) for r in entries]
                mean_l, mean_u = statistics.mean(ls), statistics.mean(us)
                losses.append(mean_l)
                utils.append(mean_u)
                loss_range.append([mean_l - min(ls), max(ls) - mean_l])
                util_range.append([mean_u - min(us), max(us) - mean_u])
                failures.append(sum(not r['feasible'] for r in entries))
                stats.append({'scenario': scenario, 'budget_level': level, 'method': method,
                              'mean_loss': mean_l, 'min_loss': min(ls), 'max_loss': max(ls),
                              'mean_maximum_budget_utilization': mean_u, 'min_utilization': min(us),
                              'max_utilization': max(us), 'infeasible_seeds': failures[-1]})
            x = np.arange(len(labels)) + (i - 1.5) * width
            options = {'width': width, 'color': colors[i], 'edgecolor': '#444444', 'linewidth': .4, 'hatch': hatches[i],
                       'error_kw': {'elinewidth': .6, 'capsize': 1.2, 'capthick': .5}}
            axes[0, column].bar(x, losses, yerr=np.asarray(loss_range).T, label=names[method], **options)
            axes[1, column].bar(x, utils, yerr=np.asarray(util_range).T, **options)
            for at, util, count in zip(x, utils, failures):
                if count:
                    axes[1, column].plot(at, util, marker='x', color='#8e2828', markersize=4, markeredgewidth=.8)
        axes[0, column].set_title('Low budget' if level == 'low' else 'High budget', fontproperties=font)
        axes[0, column].set_ylim(0, 4.3)
        axes[1, column].set_ylim(0, 1.65)
        axes[1, column].axhline(1, color='#333333', linestyle='--', linewidth=.65)
        axes[1, column].set_xticks(np.arange(len(labels)), labels, rotation=25, ha='right')
        for axis in axes[:, column]:
            axis.grid(axis='y', color='#dddddd', linewidth=.4)
            axis.set_axisbelow(True)
            for text in axis.get_xticklabels() + axis.get_yticklabels():
                text.set_fontproperties(small)
    axes[0, 0].set_ylabel('Clipped normalized age loss', fontproperties=font)
    axes[1, 0].set_ylabel('Maximum budget utilization', fontproperties=font)
    handles, legends = axes[0, 0].get_legend_handles_labels()
    figure.legend(handles, legends, loc='upper center', bbox_to_anchor=(.5, .995), ncol=4, frameon=False, prop=small,
                  handlelength=1.2, columnspacing=1)
    figure.text(.5, .026, 'Synthetic event model, four held-out seeds. Whiskers: observed range. Crosses: at least one infeasible seed.',
                ha='center', fontproperties=small)
    figure.tight_layout(rect=(0, .08, 1, .94), pad=.5)
    name = 'rolling-loss-and-budget'
    figure.savefig(args.output / f'{name}.png', dpi=300, metadata={'Software': 'Matplotlib'})
    figure.savefig(args.output / f'{name}.pdf', metadata={'Author': '', 'Creator': '', 'Title': 'Rolling loss and actual budget'})
    plt.close(figure)
    fonts = subprocess.run(['pdffonts', str(args.output / f'{name}.pdf')], check=True, capture_output=True, text=True).stdout
    font_rows = fonts.splitlines()[2:]
    if not font_rows or any('TimesNewRoman' not in row or ' yes ' not in row for row in font_rows):
        raise ValueError('PDF 未确认 Times New Roman 字体嵌入')
    (args.output / 'pdf-fonts.txt').write_text(fonts)
    with (args.output / f'{name}.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(stats[0]))
        writer.writeheader()
        writer.writerows(stats)
    sources = ('experiments/plot_rolling_reference.py', 'experiments/check_rolling_reference.py')
    root = Path(__file__).resolve().parents[1]
    for source in sources:
        target = args.output / 'executed-source' / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / source, target)
    record = {'input_manifest_sha256': hashlib.sha256((args.run / 'manifest.json').read_bytes()).hexdigest(),
              'input_summary_sha256': hashlib.sha256((args.run / 'summary.json').read_bytes()).hexdigest(),
              'source_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources},
              'font_name': font.get_name(), 'font_file': str(font_path), 'font_sha256': hashlib.sha256(font_path.read_bytes()).hexdigest(),
              'dpi': 300, 'pdf_font_type': 42, 'scope': '均值及观察范围，不是置信区间。预算率逐运行取实际资源最大值再求均值。',
              'artifact_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.iterdir() if p.is_file()}}
    (args.output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
