"""逐业务展示全部静态部署重复与候选差值条件区间。"""
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

PROFILES = (('stable', 'high', 'Stable / high'), ('shared_priority', 'high', 'Priority / high'),
            ('switch', 'low', 'Notified switch / low'), ('bursty_jitter', 'low', 'Jitter / low'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plot(analysis, output):
    manifest = json.loads((analysis / 'manifest.json').read_text())
    if manifest['status'] != 'passed' or len(manifest['cases']) != 36:
        raise ValueError('图源必须为完整静态确认')
    names = ('summary.csv', 'frozen-static.json', 'seed-repeat-diagnostics.json',
             'candidate-repeat-pairs.json', 'summary-checks.json')
    for name in names:
        if sha(analysis / name) != manifest['artifact_sha256'][name]:
            raise ValueError('图源摘要不同')
    if json.loads((analysis / 'summary-checks.json').read_text())['status'] != 'passed':
        raise ValueError('图源未通过独立核对')
    with (analysis / 'summary.csv').open() as stream:
        summaries = list(csv.DictReader(stream))
    frozen = json.loads((analysis / 'frozen-static.json').read_text())
    pairs = json.loads((analysis / 'candidate-repeat-pairs.json').read_text())
    fp = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(fp))
    font = font_manager.FontProperties(fname=fp)
    resolved = Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'), fallback_to_default=False))
    if font.get_name() != 'Times New Roman' or resolved.resolve() != fp.resolve():
        raise ValueError('Times New Roman 字体解析失败')
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 9, 'pdf.fonttype': 42,
                         'axes.unicode_minus': False})
    output.mkdir(parents=True, exist_ok=False)

    def finish(fig, name, title):
        fig.savefig(output / (name + '.png'), dpi=300, metadata={'Software': 'Matplotlib'})
        fig.savefig(output / (name + '.pdf'), metadata={'Author': '', 'Creator': '', 'Title': title})
        plt.close(fig)
        info = subprocess.run(['pdffonts', str(output / (name + '.pdf'))], capture_output=True, text=True, check=True).stdout
        if not info.splitlines()[2:] or any('TimesNewRoman' not in line or ' yes ' not in line for line in info.splitlines()[2:]):
            raise ValueError('PDF 字体嵌入不完整')
        (output / (name + '-fonts.txt')).write_text(info)

    fig, axes = plt.subplots(4, 2, figsize=(8, 10))
    for row, (s, b, label) in enumerate(PROFILES):
        selected = next(r['selected_candidate'] for r in frozen if (r['scenario'], r['budget_level']) == (s, b))
        indices = sorted({int(r['candidate_index']) for r in summaries if r['scenario'] == s})
        for col, seed in enumerate((11, 29)):
            axis = axes[row, col]
            for j, index in enumerate(indices):
                rs = sorted([r for r in summaries if (r['scenario'], int(r['seed']), int(r['candidate_index'])) ==
                            (s, seed, index)], key=lambda r: int(r['block']))
                if len(rs) != 3: raise ValueError('绘图三块重复分母错误')
                color, marker = ('#333333', 'D') if j == 0 else ('#27749c', 'o')
                offset = 0 if len(indices) == 1 else (-.07 if j == 0 else .07)
                for i, r in enumerate(rs):
                    lo, hi = float(r['loss_lower']), float(r['loss_upper'])
                    mid, x = (lo + hi) / 2, int(r['block']) + offset
                    axis.errorbar(x, mid, yerr=[[mid - lo], [hi - mid]], fmt=marker, color=color,
                                  markersize=4, capsize=3, elinewidth=.9,
                                  label=f'C{index:02d}' + (' (frozen)' if index == selected else '') if i == 0 else None)
                    if r['window_feasibility'] != 'within':
                        axis.plot(x, mid, 'x', color='#b32d2d', markersize=8)
            axis.set_title(label + f' / seed {seed}')
            axis.set_xticks([1, 2, 3])
            axis.set_xlim(.7, 3.3)
            axis.set_ylabel('Collector age loss')
            axis.ticklabel_format(axis='y', style='plain', useOffset=False)
            axis.legend(frameon=False, fontsize=8, loc='best')
            axis.grid(color='#dddddd', linewidth=.4)
            axis.set_axisbelow(True)
            if row == 3: axis.set_xlabel('Deployment block')
    fig.text(.5, .012, 'All 36 training cases. Conditional age intervals, not confidence intervals. Red crosses mark exclusions.',
             ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .035, 1, 1), pad=1)
    finish(fig, 'static-confirmation-repeats', 'Static confirmation with separate business seeds and all deployment repeats')

    fig, axes = plt.subplots(1, 2, figsize=(8, 3.7))
    for axis, s, label in zip(axes, ('shared_priority', 'bursty_jitter'), ('Priority: C03 minus C02', 'Jitter: C05 minus C01')):
        for seed, offset, color, marker in ((11, -.07, '#333333', 'D'), (29, .07, '#27749c', 'o')):
            rs = sorted([r for r in pairs if (r['scenario'], r['seed']) == (s, seed)], key=lambda r: r['block'])
            if len(rs) != 3: raise ValueError('配对区间分母错误')
            for i, r in enumerate(rs):
                lo, hi = r['alternative_minus_model_bounds']
                mid = (lo + hi) / 2
                axis.errorbar(r['block'] + offset, mid, yerr=[[mid - lo], [hi - mid]], fmt=marker, color=color,
                              markersize=4, capsize=3, elinewidth=.9, label=f'Seed {seed}' if i == 0 else None)
        axis.axhline(0, color='#666666', linewidth=.7, linestyle='--')
        axis.set_title(label)
        axis.set_xticks([1, 2, 3])
        axis.set_xlim(.7, 3.3)
        axis.set_xlabel('Deployment block')
        axis.set_ylabel('Collector age-loss difference')
        axis.ticklabel_format(axis='y', style='plain', useOffset=False)
        axis.legend(frameon=False, fontsize=8)
        axis.grid(color='#dddddd', linewidth=.4)
        axis.set_axisbelow(True)
    fig.text(.5, .015, 'Alternative minus original model. Conservative conditional difference intervals, not confidence intervals.',
             ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .06, 1, 1), pad=1)
    finish(fig, 'static-confirmation-paired-differences', 'Conditional age-loss differences of the two changed static candidates')
    for name in names:
        shutil.copyfile(analysis / name, output / name)
    source = Path(__file__).resolve()
    target = output / 'executed-source/experiments' / source.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    record = dict(status='passed', analysis_manifest_sha256=sha(analysis / 'manifest.json'),
        input_sha256={n: sha(analysis / n) for n in names}, source_sha256={'experiments/' + source.name: sha(source)},
        font_name=font.get_name(), font_file=str(fp), font_sha256=sha(fp), dpi=300,
        scope='两已消费训练业务，每配置三部署重复，全案例保留，年龄条件区间并非置信区间',
        artifact_sha256={str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()})
    (output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plot(args.analysis, args.output)
