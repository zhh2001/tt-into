"""展示全部静态候选部署重复及实际导出预算状态。"""
import argparse
import json
from pathlib import Path
import subprocess

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from scalable_contract import sha
from check_observation_wire import load


def plot(analysis, output):
    summary = load(analysis / 'summary.json')
    frozen = load(analysis / 'frozen-static.json')
    if len(summary) != 120:
        raise ValueError('图源必须包含全部确认案例')
    font = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(font))
    resolved = Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'), fallback_to_default=False))
    if resolved.resolve() != font.resolve():
        raise ValueError('未解析到真正Times New Roman')
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 9, 'pdf.fonttype': 42})
    output.mkdir(parents=True, exist_ok=False)
    networks = ('n3f2', 'n5f2', 'n8f2', 'n5f4', 'n5f8')
    scenes = ('stable', 'shared_priority')
    colors = ('#315C8C', '#BA6A35')

    def finish(figure, name):
        figure.savefig(output / (name + '.png'), dpi=300, bbox_inches='tight')
        figure.savefig(output / (name + '.pdf'), bbox_inches='tight', metadata={'Creator': 'Matplotlib', 'Title': name})
        plt.close(figure)
        fonts = subprocess.check_output(['pdffonts', str(output / (name + '.pdf'))], text=True)
        if 'TimesNewRomanPSMT' not in fonts or any('TimesNewRoman' not in line or 'yes yes yes' not in line for line in fonts.splitlines()[2:]):
            raise ValueError('PDF字体未完整嵌入')
        (output / (name + '-fonts.txt')).write_text(fonts)

    for kind in ('age', 'export'):
        figure, axes = plt.subplots(2, 5, figsize=(12.6, 5), constrained_layout=True)
        for i, scene in enumerate(scenes):
            for j, network in enumerate(networks):
                axis = axes[i, j]
                group = [r for r in summary if (r['network_id'], r['scenario']) == (network, scene)]
                candidates = sorted({r['candidate_id'] for r in group})
                cell = next(c for c in frozen['cells'] if (c['network_id'], c['scenario']) == (network, scene))
                chosen = cell['selected']['candidate_id'] if cell['selected'] else None
                for k, candidate in enumerate(candidates):
                    cases = sorted([r for r in group if r['candidate_id'] == candidate], key=lambda r: (r['block'], r['business_seed']))
                    for index, row in enumerate(cases):
                        marker = 'o' if k == 0 else 'D'
                        face = colors[k] if row['business_seed'] == 11 else 'white'
                        label = candidate + (' *' if candidate == chosen else '')
                        if kind == 'age':
                            x = row['block'] + 1 + (-.12 if k == 0 else .12) + (-.03 if row['business_seed'] == 11 else .03)
                            lower, upper = row['controller_loss']
                            middle = (lower + upper) / 2
                            axis.errorbar(x, middle, yerr=[[middle - lower], [upper - middle]], fmt=marker,
                                color=colors[k], markerfacecolor=face, markersize=4, capsize=2, elinewidth=.7,
                                label=label if index == 0 else None)
                            if row['window_feasibility'] != 'within':
                                axis.scatter([x], [middle], marker='x', color='#B02525', s=30, zorder=5)
                        else:
                            x = k + (row['block'] - 1) * .10 + (-.02 if row['business_seed'] == 11 else .02)
                            if row['export_bytes_per_s'] is not None:
                                limit = 450 * row['flows']
                                axis.scatter([x], [row['export_bytes_per_s'] / limit], marker=marker,
                                    edgecolors=colors[k], facecolors=face, s=20)
                                if row['window_feasibility'] != 'within':
                                    axis.scatter([x], [row['export_bytes_per_s'] / limit], marker='x', color='#B02525', s=30)
                            else:
                                axis.text(k, 1.09, 'N/A', ha='center', fontsize=8)
                axis.set_title(f'{network[1]} nodes / {network[3]} flows')
                axis.grid(alpha=.2)
                if kind == 'age':
                    axis.set_xticks((1, 2, 3)); axis.set_xlim(.7, 3.3); axis.set_ylim(0, 4.2)
                    axis.set_yticks((0, 1, 2, 3, 4)); axis.legend(frameon=False, fontsize=8, loc='upper right')
                    if i == 1: axis.set_xlabel('Deployment block')
                    if j == 0: axis.set_ylabel(('Stable' if i == 0 else 'Priority') + ' controller age loss')
                else:
                    axis.axhline(1, color='#666666', linestyle='--', linewidth=.8)
                    axis.set_xticks(range(len(candidates)), [c + (' *' if c == chosen else '') for c in candidates])
                    axis.set_xlim(-.3, 1.3); axis.set_ylim(0, 1.17)
                    if i == 1: axis.set_xlabel('Static candidate')
                    if j == 0: axis.set_ylabel(('Stable' if i == 0 else 'Priority') + ' export / budget')
        figure.text(.5, -.035, 'Solid: seed 11. Hollow: seed 29. *: frozen initialization. Red crosses: budget exclusions.' +
                    (' Bars: conditional age bounds.' if kind == 'age' else ''), ha='center', fontsize=8)
        finish(figure, 'static-confirmation-' + kind)
    record = dict(status='passed', script_sha256=sha(Path(__file__)),
        analysis_manifest_sha256=sha(analysis / 'manifest.json'),
        font_file=str(font), font_sha256=sha(font), png_dpi=300,
        data_sha256={n: sha(analysis / n) for n in ('summary.json', 'frozen-static.json')},
        artifact_sha256={p.name: sha(p) for p in output.iterdir() if p.is_file()})
    (output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plot(args.analysis, args.output)
