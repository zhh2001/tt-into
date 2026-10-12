"""从完整资源网格生成接纳对照图。两个数分别为离线上界和有界 TT-INT 调参。"""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, findfont
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--tuned', type=Path, help='可选的独立逐流初始化调参结果')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = args.run / 'frontier.json'
    rows = json.loads(source.read_text())
    manifest = json.loads((args.run / 'manifest.json').read_text())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if manifest['status'] != 'passed' or manifest['artifact_sha256']['frontier.json'] != digest:
        raise ValueError('来源状态或数据摘要不匹配')
    font_path = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font = FontProperties(fname=str(font_path), size=9)
    small = FontProperties(fname=str(font_path), size=8)
    if Path(findfont(font)).resolve() != font_path.resolve() or font.get_name() != 'Times New Roman':
        raise ValueError('未解析到真正的 Times New Roman')
    plt.rcParams.update({'pdf.fonttype': 42, 'font.family': 'Times New Roman'})
    shared = sorted({r['budget']['inband_per_link_bytes'][2] for r in rows})
    exports = sorted({r['budget']['export_bytes'] for r in rows})
    input_hashes = {str(source): digest}
    groups = [('Initial TT-INT grid', rows)]
    if args.tuned:
        tuned_source = args.tuned / 'results.json'
        tuned_manifest = json.loads((args.tuned / 'manifest.json').read_text())
        tuned_digest = hashlib.sha256(tuned_source.read_bytes()).hexdigest()
        if tuned_manifest['status'] != 'passed' or tuned_manifest['artifact_sha256']['results.json'] != tuned_digest:
            raise ValueError('增强调参来源状态或摘要不匹配')
        tuned_rows = json.loads(tuned_source.read_text())
        for r in tuned_rows:
            original = next(v for v in rows if v['capacity'] == r['capacity'] and v['budget'] == r['budget'])
            if original['oracle']['admitted_weight'] != r['upper_weight']:
                raise ValueError('两个网格的离线上界不一致')
            r['oracle'] = {'admitted_weight': r['upper_weight']}
        groups.append(('TT-INT initialization + per-flow tuning', tuned_rows))
        input_hashes[str(tuned_source)] = tuned_digest
    fig, axes = plt.subplots(len(groups), 2, figsize=(7.2, 2.65 * len(groups)), sharey=True, squeeze=False)
    for index, (group_name, group_rows) in enumerate(groups):
        for axis, capacity in zip(axes[index], (1, 2)):
            matrix = np.zeros((len(exports), len(shared)))
            lookup = {(r['budget']['inband_per_link_bytes'][2], r['budget']['export_bytes']): r
                      for r in group_rows if r['capacity'] == capacity}
            for y, exported in enumerate(exports):
                for x, budget in enumerate(shared):
                    row = lookup[budget, exported]
                    matrix[y, x] = row['admission_weight_gap']
            axis.imshow(matrix, cmap='Greys', vmin=0, vmax=6, origin='lower', aspect='auto', interpolation='nearest')
            for y, exported in enumerate(exports):
                for x, budget in enumerate(shared):
                    row = lookup[budget, exported]
                    native = row['native_best']['admitted_weight'] if row['native_best'] else 0
                    axis.text(x, y, f'{row["oracle"]["admitted_weight"]} / {native}',
                              ha='center', va='center', fontproperties=small)
            axis.set_xticks(range(len(shared)), shared)
            axis.set_yticks(range(len(exports)), exports)
            axis.set_xticks(np.arange(-.5, len(shared)), minor=True)
            axis.set_yticks(np.arange(-.5, len(exports)), minor=True)
            axis.grid(which='minor', color='#888888', linewidth=.35)
            axis.tick_params(which='minor', bottom=False, left=False)
            axis.set_xlabel('Shared-link budget (B)', fontproperties=font)
            axis.set_title(f'{group_name}\nK = {capacity}', fontproperties=font)
            for label in axis.get_xticklabels() + axis.get_yticklabels():
                label.set_fontproperties(small)
    for row_axes in axes:
        row_axes[0].set_ylabel('Export budget (B)', fontproperties=font)
    fig.text(.5, .025, 'Admitted weight: offline bound / TT-INT grid. Control costs excluded.',
             ha='center', fontproperties=small)
    fig.tight_layout(rect=(0, .055, 1, 1), pad=.7)
    name = 'fork-admission-budget-grid'
    fig.savefig(args.output / (name + '.png'), dpi=300, metadata={'Software': 'Matplotlib'})
    fig.savefig(args.output / (name + '.pdf'), metadata={'Author': '', 'Creator': '', 'Title': name})
    plt.close(fig)
    result = {'source': str(source), 'source_sha256': digest, 'input_sha256': input_hashes, 'font_file': str(font_path),
              'font_sha256': hashlib.sha256(font_path.read_bytes()).hexdigest(), 'dpi': 300,
              'plot_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'scope': '给定未来轨迹及有界调参的模型图；不是网络或在线性能结果',
              'artifact_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in args.output.glob('*') if p.suffix in ('.png', '.pdf')}}
    (args.output / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
