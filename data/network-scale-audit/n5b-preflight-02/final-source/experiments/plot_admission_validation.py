"""从全部执行结果绘制连续年龄界与启用、更新的序列化费用。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, findfont
import numpy as np


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs', nargs=4, type=Path, required=True,
                   help='依次为原参数诊断、原参数普通、裕量诊断、裕量普通')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    hashes, summaries = {}, []
    for run in a.runs:
        source = run / 'summary.json'
        summary = json.loads(source.read_text())
        if summary['status'] != 'passed':
            raise ValueError('执行未通过，不能用作本图的有效运行')
        for name, digest in summary['artifact_sha256'].items():
            if hashlib.sha256((run / name).read_bytes()).hexdigest() != digest:
                raise ValueError('测量产物摘要错误 ' + name)
        summaries.append(summary)
        hashes[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
    if [s['diagnostics'] for s in summaries] != [True, False, True, False]:
        raise ValueError('运行模式顺序错误')
    font_path = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font = FontProperties(fname=str(font_path), size=9)
    small = FontProperties(fname=str(font_path), size=8)
    if Path(findfont(font)).resolve() != font_path.resolve() or font.get_name() != 'Times New Roman':
        raise ValueError('没有解析到 Times New Roman')
    plt.rcParams.update({'pdf.fonttype': 42, 'font.family': 'Times New Roman'})
    a.output.mkdir(parents=True, exist_ok=False)
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.85))
    labels = ['Exact\nDiag.', 'Exact\nOrd.', 'Margin\nDiag.', 'Margin\nOrd.']
    x = np.arange(4)
    for offset, capacity, color in [(-.13, 1, '#555555'), (.13, 2, '#b97820')]:
        low, high = [], []
        for summary in summaries:
            case = next(c for c in summary['cases'] if c['capacity'] == capacity)
            task = case['age']['phases'][1]['tasks'][2]
            low.append(task['violation_lower'] * 100)
            high.append(task['violation_upper'] * 100)
        axes[0].vlines(x + offset, low, high, color=color, linewidth=2)
        axes[0].scatter(x + offset, low, marker='_', s=75, color=color, label=f'K = {capacity}')
        axes[0].scatter(x + offset, high, marker='_', s=75, color=color)
    axes[0].set_xticks(x, labels)
    axes[0].set_ylabel('Shared task deadline violation (%)', fontproperties=font)
    axes[0].set_title('Causal generation bounds, late phase', fontproperties=font)
    axes[0].set_ylim(-.7, 18)
    legend = axes[0].legend(prop=small, frameon=False)
    for t in legend.get_texts():
        t.set_fontproperties(small)
    activation, updates, control_labels = [], [], []
    for index in (1, 3):
        for case in summaries[index]['cases']:
            activation.append(sum(r['request_serialized_bytes'] + r['reply_serialized_bytes']
                                  for r in case['update_execution_checks'] if r['kind'] == 'initial_activation'))
            updates.append(sum(r['request_serialized_bytes'] + r['reply_serialized_bytes']
                               for r in case['update_execution_checks'] if r['kind'] == 'online_update'))
            control_labels.append(('Exact' if index == 1 else 'Margin') + '\nK = ' + str(case['capacity']))
    axes[1].bar(x, activation, width=.55, color='#bcbcbc', edgecolor='#444444', linewidth=.5, label='Activation')
    axes[1].bar(x, updates, bottom=activation, width=.55, color='#555555', edgecolor='#444444', linewidth=.5, label='Phase update')
    axes[1].set_xticks(x, control_labels)
    axes[1].set_ylabel('Serialized request + reply (B)', fontproperties=font)
    axes[1].set_title('Runtime control, ordinary version', fontproperties=font)
    axes[1].set_ylim(0, 1650)
    for i, (first, last) in enumerate(zip(activation, updates)):
        axes[1].text(i, first + last + 35, str(first + last), ha='center', fontproperties=small)
    axes[1].legend(prop=small, frameon=False, ncol=2, loc='upper center')
    for axis in axes:
        axis.grid(axis='y', color='#dddddd', linewidth=.4)
        axis.set_axisbelow(True)
        for t in axis.get_xticklabels() + axis.get_yticklabels():
            t.set_fontproperties(small)
    fig.text(.5, .025, 'Intervals are causal bounds, not confidence intervals. Installation and TCP frames are accounted separately.',
             ha='center', fontproperties=small)
    fig.tight_layout(rect=(0, .07, 1, 1), pad=.8)
    name = 'threshold-margin-and-control'
    fig.savefig(a.output / (name + '.png'), dpi=300, metadata={'Software': 'Matplotlib'})
    fig.savefig(a.output / (name + '.pdf'), metadata={'Author': '', 'Creator': '', 'Title': name})
    plt.close(fig)
    archive = a.output / 'executed-source/experiments'
    archive.mkdir(parents=True)
    shutil.copyfile(__file__, archive / Path(__file__).name)
    manifest = {'input_sha256': hashes, 'plot_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'font_file': str(font_path), 'font_sha256': hashlib.sha256(font_path.read_bytes()).hexdigest(),
                'dpi': 300, 'scope': '确定性软件执行核对，不是随机性能比较',
                'artifact_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in a.output.glob('*') if p.suffix in ('.png', '.pdf')}}
    (a.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
