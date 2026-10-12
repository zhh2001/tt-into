"""保留种子模型诊断图。均值和全范围来自全部预声明测试，不表示置信区间。"""
import argparse
import csv
import gzip
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.run / 'manifest.json').read_text())
    if manifest['status'] != 'passed':
        raise ValueError('不能使用失败运行绘制本图')
    source = args.run / 'heldout-results.json.gz'
    if hashlib.sha256(source.read_bytes()).hexdigest() != manifest['artifact_sha256'][source.name]:
        raise ValueError('保留测试数据摘要错误')
    data = json.loads(gzip.decompress(source.read_bytes()))
    spec = json.loads((args.run / 'specification.json').read_text())
    family_labels = {'tt': 'TT-INT tuning', 'dinto': 'D-INTO P3 adaptation',
                     'intopp': 'INTOPP adaptation', 'intopp_ahead': 'INTOPP with lookahead'}
    scenario_labels = ['Jitter', 'Burst', 'Low\ntraffic', 'Deadline\nchange', 'Report\ndelay', 'Report\nloss', 'Tight\nbudget']
    font_path = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font, small = FontProperties(fname=str(font_path), size=9), FontProperties(fname=str(font_path), size=8)
    if Path(findfont(font)).resolve() != font_path.resolve() or font.get_name() != 'Times New Roman':
        raise ValueError('字体未解析为真正 Times New Roman')
    plt.rcParams.update({'font.family': 'Times New Roman', 'pdf.fonttype': 42})
    args.output.mkdir(parents=True, exist_ok=False)
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 4.6), sharex=True)
    stats = []
    width = .18
    colors, hatches = ['#eeeeee', '#8ba6bd', '#bdbdbd', '#bf914a'], ['', '//', '..', 'xx']
    for column, capacity in enumerate(spec['capacities']):
        for index, (family, label) in enumerate(family_labels.items()):
            means, ranges, violations = [], [], []
            for scenario in spec['scenarios']:
                entries = [r['result'] for r in data if r['capacity'] == capacity and r['family'] == family and r['scenario'] == scenario]
                if len(entries) != len(spec['heldout_seeds']):
                    raise ValueError('预声明测试记录缺失')
                values = [r['budget_qualified_weight'] for r in entries]
                mean = sum(values) / len(values)
                means.append(mean)
                ranges.append((mean - min(values), max(values) - mean))
                bad = 100 * sum(r['weighted_violation_fraction'] for r in entries) / len(entries)
                violations.append(bad)
                stats.append({'capacity': capacity, 'family': family, 'scenario': scenario, 'seeds': len(entries),
                              'mean_budget_qualified_weight': mean, 'min_budget_qualified_weight': min(values),
                              'max_budget_qualified_weight': max(values), 'mean_weighted_violation_percent': bad,
                              'mean_runtime_api_serialized_bytes': sum(r['runtime_api_total_serialized_bytes'] for r in entries) / len(entries)})
            x = np.arange(len(spec['scenarios'])) + (index - 1.5) * width
            axes[0, column].bar(x, means, width=width, color=colors[index], edgecolor='#444444', linewidth=.4,
                                hatch=hatches[index], label=label, yerr=np.asarray(ranges).T,
                                error_kw={'elinewidth': .5, 'capsize': 1.3, 'capthick': .5})
            axes[1, column].bar(x, violations, width=width, color=colors[index], edgecolor='#444444', linewidth=.4, hatch=hatches[index])
        axes[0, column].set_title(f'K = {capacity}', fontproperties=font)
        axes[0, column].set_ylim(0, 5.6)
        axes[1, column].set_ylim(0, 52)
        axes[1, column].set_xticks(np.arange(len(spec['scenarios'])), scenario_labels)
        for axis in axes[:, column]:
            axis.set_axisbelow(True)
            axis.grid(axis='y', color='#dddddd', linewidth=.4)
            for text in axis.get_xticklabels() + axis.get_yticklabels():
                text.set_fontproperties(small)
    axes[0, 0].set_ylabel('Weight fulfilled within budgets', fontproperties=font)
    axes[1, 0].set_ylabel('Weighted deadline violation (%)', fontproperties=font)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(.5, 1), ncol=2, prop=small, frameon=False)
    fig.text(.5, .025, 'Conditional event model, 20 held-out seeds per scenario. Whiskers show the observed minimum and maximum.',
             ha='center', fontproperties=small)
    fig.tight_layout(rect=(0, .07, 1, .91), pad=.6)
    name = 'heldout-task-feasibility'
    fig.savefig(args.output / (name + '.png'), dpi=300, metadata={'Software': 'Matplotlib'})
    fig.savefig(args.output / (name + '.pdf'), metadata={'Author': '', 'Creator': '', 'Title': name})
    plt.close(fig)
    with (args.output / (name + '.csv')).open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(stats[0]))
        writer.writeheader()
        writer.writerows(stats)
    archive = args.output / 'executed-source/experiments'
    archive.mkdir(parents=True)
    shutil.copyfile(__file__, archive / Path(__file__).name)
    result = {'input_sha256': {str(source): hashlib.sha256(source.read_bytes()).hexdigest()},
              'plot_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'font_file': str(font_path), 'font_sha256': hashlib.sha256(font_path.read_bytes()).hexdigest(), 'dpi': 300,
              'scope': '条件事件模型的保留种子均值与观察范围，非网络测量或统计显著性结论',
              'artifact_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.iterdir() if p.is_file()}}
    (args.output / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
