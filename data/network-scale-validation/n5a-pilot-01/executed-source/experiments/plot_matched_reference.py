"""从已独立验收的完整矩阵导出主图和全部方法达标图。"""
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
import numpy as np

from check_rolling_reference import load


LABELS = {'static': 'Tuned static TT-INT', 'feedback': 'Uniform feedback', 'local': 'Uniform local',
          'joint': 'Uniform joint', 'phase_joint': 'Temporal joint', 'uniform_guard': 'Uniform repair',
          'phase_guard': 'Temporal repair', 'phase_local_guard': 'Temporal local repair'}
SCENARIOS = {'stable': 'Stable', 'bursty': 'Burst', 'shared_priority': 'Priority', 'switch': 'Notified switch',
             'sparse_stop': 'Sparse stop', 'report_impairment': 'Report impairment', 'budget_floor': 'Budget floor',
             'bursty_jitter': 'Jitter', 'unannounced_switch': 'Unannounced switch'}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest, analysis = load(args.run / 'manifest.json'), load(args.analysis / 'manifest.json')
    if analysis['status'] != 'passed' or analysis['input_manifest_sha256'] != sha(args.run / 'manifest.json'):
        raise ValueError('图源未经核对或运行清单已变化')
    if analysis['artifact_sha256']['aggregate.csv'] != sha(args.analysis / 'aggregate.csv'):
        raise ValueError('图源汇总摘要错误')
    with (args.analysis / 'aggregate.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 144:
        raise ValueError('完整矩阵汇总分母错误')
    def row(s, b, m):
        result = [r for r in rows if (r['scenario'], r['budget_level'], r['method']) == (s, b, m)]
        if len(result) != 1 or int(result[0]['seed_count']) != 4:
            raise ValueError('图中场景缺失或配对种子错误')
        return result[0]
    font_path = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(font_path))
    font = font_manager.FontProperties(fname=font_path)
    if font.get_name() != 'Times New Roman' or Path(font_manager.findfont(font, fallback_to_default=False)).resolve() != font_path.resolve():
        raise ValueError('字体文件解析错误')
    if Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'), fallback_to_default=False)).resolve() != font_path.resolve():
        raise ValueError('字体族回退错误')
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 9, 'pdf.fonttype': 42, 'axes.unicode_minus': False})
    args.output.mkdir(parents=True, exist_ok=False)
    def finish(fig, name, title):
        fig.savefig(args.output / f'{name}.png', dpi=300, metadata={'Software': 'Matplotlib'})
        fig.savefig(args.output / f'{name}.pdf', metadata={'Author': '', 'Creator': '', 'Title': title})
        plt.close(fig)
        fonts = subprocess.run(['pdffonts', str(args.output / f'{name}.pdf')], check=True, text=True, capture_output=True).stdout
        if not fonts.splitlines()[2:] or any('TimesNewRoman' not in line or ' yes ' not in line for line in fonts.splitlines()[2:]):
            raise ValueError('PDF 未嵌入要求字体')
        (args.output / f'{name}-fonts.txt').write_text(fonts)
    methods = ('static', 'joint', 'phase_guard', 'phase_local_guard')
    colors, symbols = ('#333333', '#27749c', '#ad6824', '#588443'), ('o', 's', '^', 'D')
    fig, axes = plt.subplots(1, 2, figsize=(7.8, 4.6), sharey=True)
    for column, budget in enumerate(manifest['budgets']):
        axis = axes[column]
        for index, method in enumerate(methods):
            rs = [row(s, budget, method) for s in manifest['scenarios']]
            mean = np.array([float(r['mean_loss']) for r in rs])
            ranges = np.array([[v - float(r['min_loss']), float(r['max_loss']) - v] for v, r in zip(mean, rs)]).T
            y = np.arange(9) + (index - 1.5) * .15
            axis.errorbar(mean, y, xerr=ranges, fmt=symbols[index], color=colors[index], markersize=3.5,
                          elinewidth=.7, capsize=1.5, label=LABELS[method])
            for x, at, r in zip(mean, y, rs):
                if int(r['total_window_feasible_seeds']) < 4:
                    axis.plot(x, at, 'x', color='#b32d2d', markersize=6, markeredgewidth=.8)
        axis.set_title('Low budget' if budget == 'low' else 'High budget')
        axis.set_xlim(0, 4.15)
        axis.set_xticks(range(5))
        axis.set_yticks(np.arange(9), [SCENARIOS[s] for s in manifest['scenarios']])
        axis.set_xlabel('Clipped normalized age loss')
        axis.invert_yaxis() if column == 0 else None
        axis.grid(axis='x', color='#dedede', linewidth=.4)
        axis.set_axisbelow(True)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', ncol=2, frameon=False, fontsize=8)
    fig.text(.5, .025, 'Synthetic event model. Bars: four-seed range. Red crosses: at least one total-window budget failure.',
             ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .08, 1, .87), pad=.7)
    finish(fig, 'matched-age-loss', 'Matched-grid event model age loss')
    # 全部方法和所有超限点保留，颜色只表达达标数，不表达损失优劣。
    cases = [(s, b) for s in manifest['scenarios'] for b in manifest['budgets']]
    values = np.array([[int(row(s, b, method)['total_window_feasible_seeds']) for method in manifest['methods']] for s, b in cases])
    fig, axis = plt.subplots(figsize=(7.8, 5.5))
    axis.imshow(values, cmap='YlGn', vmin=0, vmax=4, aspect='auto')
    axis.set_xticks(np.arange(8), [LABELS[m] for m in manifest['methods']], rotation=35, ha='right', fontsize=8)
    axis.set_yticks(np.arange(18), [f'{SCENARIOS[s]} / {b}' for s, b in cases], fontsize=8)
    for (i, j), count in np.ndenumerate(values):
        axis.text(j, i, f'{count}/4', ha='center', va='center', color='white' if count >= 3 else 'black', fontsize=8)
    axis.set_title('Total-window budget feasibility, all methods')
    fig.text(.5, .015, 'Data links, export attempts, control API and local notices checked separately. Synthetic event model.',
             ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .05, 1, 1), pad=.65)
    finish(fig, 'matched-budget-feasibility', 'Matched-grid event model budget feasibility')
    shutil.copyfile(args.analysis / 'aggregate.csv', args.output / 'plot-data.csv')
    root = Path(__file__).resolve().parents[1]
    sources = ('experiments/plot_matched_reference.py', 'experiments/check_rolling_reference.py')
    for name in sources:
        target = args.output / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    record = {'status': 'passed', 'input_manifest_sha256': sha(args.run / 'manifest.json'),
              'analysis_manifest_sha256': sha(args.analysis / 'manifest.json'),
              'source_sha256': {n: sha(root / n) for n in sources},
              'font_name': font.get_name(), 'font_file': str(font_path), 'font_sha256': sha(font_path), 'dpi': 300,
              'scope': '均值与四种子观察范围，全部失败点保留，不作统计显著性推断',
              'artifact_sha256': {p.name: sha(p) for p in args.output.iterdir() if p.is_file()}}
    (args.output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
