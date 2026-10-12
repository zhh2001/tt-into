"""绘制完整有限静态费用挑战和训练前后控制器年龄界。"""
import argparse
from pathlib import Path
import shutil
import subprocess

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D

from analyze_observer_training import write
from check_matched_network import load, sha

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ('stable', 'shared_priority', 'switch', 'bursty_jitter')
LABELS = ('Stable / high', 'Priority / high', 'Switch / low', 'Bursty jitter / low')


def worst_bounds(score):
    bounds = score['controller_loss_bounds'].values()
    return max(r[0] for r in bounds), max(r[1] for r in bounds)


def plot(analysis, checks, output):
    manifest, audit = load(analysis / 'manifest.json'), load(checks)
    if audit['status'] != 'passed' or audit['cases'] != 192 or audit['analysis_manifest_sha256'] != sha(analysis / 'manifest.json'):
        raise ValueError('绘图需要完整且独立通过的192例训练')
    for name in ('candidate-scores.json', 'training-selections.json', 'finite-frontier.json'):
        if sha(analysis / name) != manifest['artifact_sha256'][name]: raise ValueError('训练图源摘要改变')
    scores, winners = load(analysis / 'candidate-scores.json'), load(analysis / 'training-selections.json')
    fp = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(fp)); font = font_manager.FontProperties(fname=fp)
    if font.get_name() != 'Times New Roman' or Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'), fallback_to_default=False)).resolve() != fp.resolve():
        raise ValueError('Times New Roman字体解析失败')
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 9, 'pdf.fonttype': 42, 'axes.unicode_minus': False})
    output.mkdir(parents=True, exist_ok=False)

    def finish(fig, name, title):
        fig.savefig(output / (name + '.png'), dpi=300, metadata={'Software': 'Matplotlib'})
        fig.savefig(output / (name + '.pdf'), metadata={'Author': '', 'Creator': '', 'Title': title})
        plt.close(fig)
        fonts = subprocess.check_output(['pdffonts', str(output / (name + '.pdf'))], text=True)
        if not fonts.splitlines()[2:] or any('TimesNewRoman' not in r or ' yes ' not in r for r in fonts.splitlines()[2:]):
            raise ValueError('PDF字体嵌入不正确')
        (output / (name + '-fonts.txt')).write_text(fonts)

    fig, axes = plt.subplots(2, 2, figsize=(8.4, 6.5))
    for axis, scenario, title in zip(axes.flat, PROFILES, LABELS):
        group = [s for s in scores if s['scenario'] == scenario]
        winner = next(r for r in winners if r['scenario'] == scenario)
        for score in group:
            if not score['frontier_evaluable']: continue
            x = score['worst_rates']['export']; lo, hi = worst_bounds(score)
            color, marker = ('#286f95', 'o') if score['feasible'] else ('#ae4839', 'x')
            axis.errorbar(x, hi, yerr=[[hi-lo], [0]], fmt=marker, color=color, markersize=4, capsize=2, linewidth=.6)
            if score['finite_subset_nondominated']:
                axis.scatter([x], [hi], facecolors='none', edgecolors='#352e4a', s=65, linewidths=.7)
            if score['candidate_index'] == winner['selected_candidate']:
                axis.scatter([x], [hi], color='#222222', marker='*', s=100, zorder=5)
                axis.annotate(f"C{score['candidate_index']:02d}", (x, hi), xytext=(6, 7), textcoords='offset points', fontsize=8)
        axis.axvline(900 if 'high' in title else 540, color='#666666', linestyle=':', linewidth=.8)
        axis.set_title(title); axis.set_xlabel('Worst export upper rate (B/s)'); axis.set_ylabel('Worst controller age-loss upper bound')
        axis.grid(linewidth=.4, color='#dddddd'); axis.set_axisbelow(True)
    handles = [Line2D([], [], marker='o', color='#286f95', linestyle='none', label='Within both training businesses'),
               Line2D([], [], marker='x', color='#ae4839', linestyle='none', label='Budget failed / ambiguous'),
               Line2D([], [], marker='o', markerfacecolor='none', color='#352e4a', linestyle='none', label='Six-layer nondominated'),
               Line2D([], [], marker='*', color='#222222', linestyle='none', label='Training selection')]
    fig.legend(handles=handles, loc='upper center', ncol=2, frameon=False, fontsize=8)
    fig.text(.5, .018, 'All 24 candidates per cell retained. Export is one projection of six-layer costs; bounds are conditional.', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .05, 1, .93)); finish(fig, 'finite-static-cost-challenge', 'Finite static cost challenge at the controller observer')

    fig, axis = plt.subplots(figsize=(7, 3.9))
    for i, scenario in enumerate(PROFILES):
        winner = next(r for r in winners if r['scenario'] == scenario)
        prior = next(s for s in scores if s['scenario'] == scenario and s['config'] == winner['previous_common_initialization'])
        selected = next((s for s in scores if s['scenario'] == scenario and s['candidate_index'] == winner['selected_candidate']), None)
        for j, score in enumerate((prior, selected)):
            if score is None: continue
            lo, hi = worst_bounds(score); mid = (lo + hi) / 2; x = i + (j - .5) * .30
            axis.bar(x, mid, width=.27, color=('#286f95', '#b46b30')[j], label=('Previous common initialization', 'Controller training selection')[j] if i == 0 else None)
            axis.errorbar(x, mid, yerr=[[mid-lo], [hi-mid]], fmt='none', color='#222222', capsize=3, linewidth=.7)
            axis.text(x, hi + .015, f"C{score['candidate_index']:02d}", ha='center', fontsize=8)
            if not score['feasible']: axis.text(x, hi + .06, '!', ha='center', fontsize=9)
    axis.set_xticks(range(4), LABELS, rotation=15, ha='right')
    axis.set_ylabel('Worst controller age-loss conditional bounds'); axis.legend(frameon=False, fontsize=8, loc='upper left')
    axis.grid(axis='y', linewidth=.4, color='#dddddd'); axis.set_axisbelow(True)
    fig.text(.5, .017, 'Consumed businesses 11/29, one deployment each. Selected configurations await repeat confirmation.', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .07, 1, 1)); finish(fig, 'controller-training-selection', 'Finite controller observer training selections')
    source = 'experiments/plot_observer_training.py'; target = output / 'executed-source' / source
    target.parent.mkdir(parents=True); shutil.copyfile(ROOT / source, target)
    write(output / 'manifest.json', {'status': 'passed', 'analysis_manifest_sha256': sha(analysis / 'manifest.json'),
        'independent_checks_sha256': sha(checks), 'font_name': font.get_name(), 'font_file': str(fp), 'font_sha256': sha(fp),
        'source_sha256': {source: sha(ROOT / source)},
        'artifact_sha256': {str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('analysis', 'checks', 'output'): p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args(); plot(a.analysis, a.checks, a.output)
