"""从全部预设评分与六层实际费用绘制文献选参结果。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
FONT = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
LABELS = {'intopp_due': 'INTOPP-TT', 'd_into_tt': 'D-INTO-TT'}


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def plot(data, selection, output):
    if not FONT.is_file(): raise ValueError('缺少Times New Roman')
    for p in FONT.parent.glob('Times_New_Roman*.ttf'): font_manager.fontManager.addfont(str(p))
    resolved = Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'), fallback_to_default=False))
    if resolved.resolve() != FONT.resolve(): raise ValueError('字体发生回退')
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 10, 'pdf.fonttype': 42,
                         'axes.unicode_minus': False, 'savefig.dpi': 300})
    chosen = json.loads((selection / 'selection.json').read_text())
    with (data / 'summary.csv').open() as stream: rows = list(csv.DictReader(stream))
    output.mkdir(parents=True, exist_ok=False)
    saved, coverage = [], []
    def export(fig, name):
        fig.tight_layout()
        fig.savefig(output / (name + '.png'), dpi=300)
        fig.savefig(output / (name + '.pdf'), metadata={'Creator': 'TT-INTO experiments', 'Author': None})
        plt.close(fig); saved.extend([name + '.png', name + '.pdf'])
    fig, axes = plt.subplots(1, 2, figsize=(7.1, 2.9))
    for color, (method, label) in enumerate(LABELS.items()):
        scores = sorted((r for r in chosen['scores'] if r['method'] == method), key=lambda r: r['preset_index'])
        for ax, column in zip(axes, (1, 3)):
            x, y = [r['preset_index'] for r in scores], [r['score'][column] for r in scores]
            ax.plot(x, y, marker='o', label=label, color=f'C{color}')
            for r in scores:
                if r['preset_index'] in chosen['selected_indices'][method]:
                    ax.scatter(r['preset_index'], r['score'][column], marker='*', s=100, color=f'C{color}', zorder=4)
            ax.set_xlabel('Preset index'); ax.set_xticks(range(6)); ax.grid(alpha=.2)
        for r in scores:
            if r['score'][0]: axes[0].annotate(f"{r['score'][0]} not within", (r['preset_index'], r['score'][1]), xytext=(0, 7), textcoords='offset points', fontsize=8)
    axes[0].set_ylabel('Worst profile mean age loss (upper)')
    axes[1].set_ylabel('Mean API serialized bytes/s')
    axes[0].legend(frameon=False, fontsize=9)
    export(fig, '01-global-parameter-scores')
    fig, axes = plt.subplots(2, 3, figsize=(7.1, 4.4))
    layers = [('e1_upper_bytes_per_s', 'Link e1'), ('e2_upper_bytes_per_s', 'Link e2'),
              ('e3_upper_bytes_per_s', 'Link e3'), ('export_upper_bytes_per_s', 'Export attempts'),
              ('api_bytes_per_s', 'API serialized'), ('local_notice_bytes_per_s', 'Local notification payload')]
    for ax, (field, title) in zip(axes.flat, layers):
        for color, (method, label) in enumerate(LABELS.items()):
            indices = sorted({int(r['preset_index']) for r in rows if r['method'] == method})
            means = []
            for index in indices:
                group = [r for r in rows if r['method'] == method and int(r['preset_index']) == index]
                values = [float(r[field]) for r in group if r[field] != '']
                means.append(statistics.mean(values) if values else float('nan'))
                coverage.append(dict(method=method, preset_index=index, layer=field, available=len(values), total=len(group)))
                if len(values) != len(group):
                    ax.annotate(f'{len(values)}/{len(group)}', (index, means[-1]), xytext=(0, 5), textcoords='offset points', fontsize=8)
            ax.plot(indices, means, marker='o', ms=3, label=label, color=f'C{color}')
        ax.set_title(title, fontsize=10); ax.set_xlabel('Preset index'); ax.set_xticks(range(6)); ax.set_ylabel('Mean bytes/s')
        ax.grid(alpha=.2)
    axes[0, 0].legend(frameon=False, fontsize=8)
    export(fig, '02-six-layer-costs')
    shutil.copyfile(ROOT / 'experiments/plot_literature_training.py', output / 'plot-source.py')
    record = dict(status='generated', stage=chosen['stage'], font_family='Times New Roman', png_dpi=300,
        font_sha256=sha(resolved), data_manifest_sha256=sha(data / 'manifest.json'),
        summary_sha256=sha(data / 'summary.csv'), selection_manifest_sha256=sha(selection / 'manifest.json'),
        selection_sha256=sha(selection / 'selection.json'), plot_source_sha256=sha(output / 'plot-source.py'),
        cost_denominators=coverage, star_means='按全局预声明排序选中，非统计显著性',
        cost_scope='帧层仅可评价窗口均值并标注缺失分母；API及本地通知保留全部运行，不同层分列',
        artifact_sha256={name: sha(output / name) for name in saved})
    (output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps(dict(status='generated', figures=2, stage=chosen['stage'])))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('data', 'selection', 'output'): p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args(); plot(a.data, a.selection, a.output)
