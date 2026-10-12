"""从已验收的完整重复校准绘制通知可用性与报告等待。"""
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


LABELS = {'static': 'Model-tuned static', 'feedback': 'Uniform feedback', 'local': 'Uniform local',
          'joint': 'Uniform joint', 'phase_joint': 'Temporal joint', 'uniform_guard': 'Uniform repair',
          'phase_guard': 'Temporal repair', 'phase_local_guard': 'Temporal local repair'}
PROFILES = (('stable', 'high', 'Stable / high'), ('shared_priority', 'high', 'Priority / high'),
            ('switch', 'low', 'Notified switch / low'), ('bursty_jitter', 'low', 'Jitter / low'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.analysis / 'manifest.json').read_text())
    if manifest['status'] != 'passed' or manifest['blocks'] != 3 or len(manifest['cases']) != 96:
        raise ValueError('要求完整三个开发块')
    names = ('block-aggregate.csv', 'report-availability.csv', 'summary-checks.json', 'case-availability.csv')
    for name in names:
        if sha(args.analysis / name) != manifest['artifact_sha256'][name]:
            raise ValueError('图源摘要错误')
    if json.loads((args.analysis / 'summary-checks.json').read_text())['status'] != 'passed':
        raise ValueError('图源汇总未经独立核对')
    with (args.analysis / names[0]).open() as stream:
        aggregate = list(csv.DictReader(stream))
    with (args.analysis / names[1]).open() as stream:
        available = list(csv.DictReader(stream))
    font_path = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(font_path))
    font = font_manager.FontProperties(fname=font_path)
    if font.get_name() != 'Times New Roman' or Path(font_manager.findfont(
            font_manager.FontProperties(family='Times New Roman'), fallback_to_default=False)).resolve() != font_path.resolve():
        raise ValueError('字体解析错误')
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 9,
                         'pdf.fonttype': 42, 'axes.unicode_minus': False})
    args.output.mkdir(parents=True, exist_ok=False)
    def finish(fig, name, title):
        fig.savefig(args.output / f'{name}.png', dpi=300, metadata={'Software': 'Matplotlib'})
        fig.savefig(args.output / f'{name}.pdf', metadata={'Author': '', 'Creator': '', 'Title': title})
        plt.close(fig)
        fonts = subprocess.run(['pdffonts', str(args.output / f'{name}.pdf')], check=True, text=True, capture_output=True).stdout
        if not fonts.splitlines()[2:] or any('TimesNewRoman' not in line or ' yes ' not in line for line in fonts.splitlines()[2:]):
            raise ValueError('PDF 字体嵌入错误')
        (args.output / f'{name}-fonts.txt').write_text(fonts)
    def row(s, b, method):
        matches = [r for r in aggregate if (r['scenario'], r['budget_level'], r['method']) == (s, b, method)]
        if len(matches) != 1 or int(matches[0]['case_count']) != 3:
            raise ValueError('图源单元分母错误')
        return matches[0]
    fig, axes = plt.subplots(1, 2, figsize=(8, 4.8), gridspec_kw={'width_ratios': [1, 1.2]})
    counts = np.array([[int(row(s, b, m)['initial_notice_first_cycle_cases']) for s, b, _ in PROFILES]
                       for m in manifest['methods']])
    axes[0].imshow(counts, cmap='Blues', vmin=0, vmax=3, aspect='auto')
    axes[0].set_yticks(range(8), [LABELS[m] for m in manifest['methods']], fontsize=8)
    axes[0].set_xticks(range(4), [label for _, _, label in PROFILES], rotation=30, ha='right', fontsize=8)
    for (i, j), value in np.ndenumerate(counts):
        axes[0].text(j, i, f'{value}/3', ha='center', va='center', color='white' if value >= 2 else 'black', fontsize=8)
    axes[0].set_title('Initial task visible in first cycle', fontsize=9)
    colors, styles = ('#333333', '#27749c', '#ad6824', '#588443'), ('-', '--', '-.', ':')
    for at, (scenario, level, label) in enumerate(PROFILES):
        values = sorted(float(r['receipt_to_entry_upper_ms']) for r in available
                        if (r['scenario'], r['budget_level']) == (scenario, level))
        axes[1].step(values, np.arange(1, len(values) + 1) / len(values), where='post',
                     color=colors[at], linestyle=styles[at], linewidth=1, label=label)
    axes[1].set_xlim(0, max(float(r['receipt_to_entry_upper_ms']) for r in available) * 1.02)
    axes[1].set_ylim(0, 1)
    axes[1].set_xlabel('Collector receipt to view entry upper bound (ms)', fontsize=8)
    axes[1].set_ylabel('Observed report fraction', fontsize=8)
    axes[1].set_title('Actual report availability', fontsize=9)
    axes[1].legend(loc='lower right', frameon=False, fontsize=8)
    axes[1].grid(color='#dedede', linewidth=.4)
    axes[1].set_axisbelow(True)
    fig.text(.5, .04, 'Development seed 29. Three complete blocks. Report fractions are descriptive, not independent repeats.',
             ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .085, 1, 1), pad=.7)
    finish(fig, 'network-view-availability', 'Actual view availability in BMv2 development calibration')
    # 两种观察位置分列，范围覆盖完整案例及条件区间，不称置信区间。
    fig, axes = plt.subplots(2, 2, figsize=(8, 7), sharex=True)
    for axis, (s, b, label) in zip(axes.flat, PROFILES):
        for at, (side, color, marker, display) in enumerate((('collector', '#27749c', 'o', 'Collector receipt'),
                                                         ('controller', '#ad6824', 's', 'Controller view'))):
            for index, method in enumerate(manifest['methods']):
                r = row(s, b, method)
                if side == 'collector':
                    low, high = float(r['loss_lower_mean']), float(r['loss_upper_mean'])
                else:
                    low, high = float(r['controller_loss_lower_mean']), float(r['controller_loss_upper_mean'])
                midpoint = (low + high) / 2
                axis.errorbar(midpoint, index + (at - .5) * .20, xerr=[[midpoint - low], [high - midpoint]],
                              fmt=marker, color=color, markersize=3, elinewidth=.9, capsize=1.6,
                              label=display if index == 0 else None)
                if int(r['within_cases']) < 3:
                    axis.plot(midpoint, index + (at - .5) * .20, 'x', color='#b32d2d', markersize=6, markeredgewidth=.8)
        axis.set_yticks(range(8), [LABELS[m] for m in manifest['methods']], fontsize=8)
        axis.invert_yaxis()
        axis.set_title(label)
        axis.grid(axis='x', color='#dedede', linewidth=.4)
        axis.set_axisbelow(True)
    for axis in axes[1]:
        axis.set_xlabel('Clipped normalized age loss')
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', frameon=False, ncol=2, fontsize=8)
    fig.text(.5, .02, 'Conditional three-case mean intervals. Red crosses: a total-window budget failure. Development data.',
             ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .055, 1, .94), pad=.7)
    finish(fig, 'network-age-observation-points', 'Collector and controller-view age in development calibration')
    for name in names[:2]:
        shutil.copyfile(args.analysis / name, args.output / name)
    source = Path(__file__).resolve()
    target = args.output / 'executed-source/experiments' / source.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    record = {'status': 'passed', 'analysis_manifest_sha256': sha(args.analysis / 'manifest.json'),
              'input_sha256': {n: sha(args.analysis / n) for n in names},
              'source_sha256': {'experiments/' + source.name: sha(source)}, 'font_name': font.get_name(),
              'font_file': str(font_path), 'font_sha256': sha(font_path), 'dpi': 300,
              'scope': '已消费业务的三块开发观察；接收和视图分列，非独立业务种子或统计排名',
              'artifact_sha256': {str(p.relative_to(args.output)): sha(p) for p in args.output.rglob('*') if p.is_file()}}
    (args.output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
