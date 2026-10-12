"""从正确性试点实际费用与确认队列生成TNR PNG和嵌入字体PDF。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
FONT = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
PROFILES = ('stable', 'shared_priority', 'switch', 'bursty_jitter')
LABELS = ('Stable', 'Priority', 'Switch', 'Bursty')
METHODS = ('intopp_due', 'd_into_tt')
METHOD_LABELS = ('INTOPP-TT', 'D-INTO-TT')


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def plot(data, output):
    if not FONT.is_file(): raise ValueError('缺少Times New Roman')
    for path in FONT.parent.glob('Times_New_Roman*.ttf'): font_manager.fontManager.addfont(str(path))
    family = font_manager.FontProperties(fname=str(FONT)).get_name()
    resolved = Path(font_manager.findfont(font_manager.FontProperties(family=family), fallback_to_default=False))
    if family != 'Times New Roman' or resolved.resolve() != FONT.resolve(): raise ValueError('字体解析不符')
    plt.rcParams.update({'font.family': family, 'font.size': 10, 'pdf.fonttype': 42, 'ps.fonttype': 42,
                         'axes.unicode_minus': False, 'savefig.dpi': 300})
    with (data / 'summary.csv').open() as stream: summaries = list(csv.DictReader(stream))
    lookup = {(r['scenario'], r['method']): r for r in summaries}
    output.mkdir(parents=True, exist_ok=False)
    saved = []
    def export(fig, name):
        fig.tight_layout()
        fig.savefig(output / (name + '.png'), dpi=300)
        fig.savefig(output / (name + '.pdf'), metadata={'Creator': 'TT-INTO experiments', 'Author': None})
        plt.close(fig)
        saved.extend([name + '.png', name + '.pdf'])
    fig, axes = plt.subplots(1, 2, figsize=(7.1, 2.7))
    for ax, field, label in zip(axes, ('api_bytes_per_s', 'local_notice_bytes_per_s'),
                               ('API serialized bytes/s', 'Local notification payload bytes/s')):
        for i, method in enumerate(METHODS):
            ax.bar([j + (i - .5) * .34 for j in range(4)],
                   [float(lookup[p, method][field]) for p in PROFILES], width=.32, label=METHOD_LABELS[i])
        ax.set_xticks(range(4), LABELS); ax.set_ylabel(label); ax.grid(axis='y', alpha=.2)
    axes[0].legend(frameon=False, fontsize=9)
    export(fig, '01-control-costs')
    fig, axes = plt.subplots(2, 1, figsize=(7.1, 4.3), sharex=True)
    inputs = {'summary.csv': sha(data / 'summary.csv'), 'manifest.json': sha(data / 'manifest.json')}
    for profile, label in zip(PROFILES, LABELS):
        r = lookup[profile, 'd_into_tt']
        path = data / r['case'] / 'decisions.jsonl'
        inputs[str(path.relative_to(data))] = sha(path)
        decisions = [json.loads(line) for line in path.read_text().splitlines() if line]
        decisions = [d for d in decisions if 'literature_state' in d]
        times = [d['time_us'] / 1e6 for d in decisions]
        axes[0].step(times, [d['literature_state']['queue_after'] for d in decisions], where='post', label=label)
        axes[1].plot(times, [d['literature_state']['settled_change_bytes'] for d in decisions], marker='o', ms=3, label=label)
    axes[0].set_ylabel('Virtual queue (byte units)'); axes[0].legend(frameon=False, ncol=4, fontsize=9)
    axes[1].axhline(12, color='black', linestyle=':', linewidth=.8, label='Mean change target')
    axes[1].set_ylabel('Confirmed assignment change (B)'); axes[1].set_xlabel('Decision cutoff (s)')
    for ax in axes: ax.grid(alpha=.2)
    export(fig, '02-confirmed-queue')
    shutil.copyfile(ROOT / 'experiments/plot_literature_pilot.py', output / 'plot-source.py')
    record = dict(status='generated', scope='训练11正确性试点的实际控制费用和确认结算，无性能排名',
        inputs_sha256=inputs, font_path=str(resolved), font_sha256=sha(resolved), family=family, png_dpi=300,
        plot_source_sha256=sha(output / 'plot-source.py'), artifact_sha256={n: sha(output / n) for n in saved})
    (output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(status='generated', figures=2, family=family)))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); plot(a.data, a.output)
