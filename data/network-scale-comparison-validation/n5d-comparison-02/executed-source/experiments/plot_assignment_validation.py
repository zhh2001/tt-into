"""从实测核对结果绘制年龄界与 sink 导出账本，保留全部配置的统计表。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, findfont
import numpy as np


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ordinary', type=Path, required=True)
    parser.add_argument('--diagnostic', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summaries, hashes = [], {}
    for directory in (args.ordinary, args.diagnostic):
        summary = json.loads((directory / 'summary.json').read_text())
        if summary['status'] != 'passed' or not summary['checks']['passed']:
            raise ValueError('只有独立核对通过的执行可用于本图')
        for name, digest in summary['artifact_sha256'].items():
            if sha(directory / name) != digest:
                raise ValueError('核对产物摘要错误 ' + name)
        summaries.append(summary)
        hashes[str(directory / 'summary.json')] = sha(directory / 'summary.json')
    if [r['diagnostics'] for r in summaries] != [False, True]:
        raise ValueError('普通与诊断版本顺序错误')
    font_path = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font, small = FontProperties(fname=str(font_path), size=9), FontProperties(fname=str(font_path), size=8)
    if Path(findfont(font)).resolve() != font_path.resolve() or font.get_name() != 'Times New Roman':
        raise ValueError('没有解析到真正 Times New Roman')
    plt.rcParams.update({'pdf.fonttype': 42, 'font.family': 'Times New Roman'})
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    statistics = []
    for summary in summaries:
        for case in summary['cases']:
            fee = sum(r['request_serialized_bytes'] + r['reply_serialized_bytes'] for r in case['runtime_api'].values())
            for task in case['age']['tasks']:
                statistics.append({'version': 'diagnostic' if summary['diagnostics'] else 'ordinary',
                    'case': case['name'], 'node': task['node'], 'violation_lower_percent': task['violation_lower'] * 100,
                    'violation_upper_percent': task['violation_upper'] * 100,
                    'emitted_reports': case['export_delivery']['emitted_reports'],
                    'missing_reports': case['export_delivery']['missing_reports'],
                    'export_charged_padded_bytes': case['measured_cost']['export_padded_bytes'],
                    'data_byte_hops_with_padding': case['measured_cost']['data_total_byte_hops_with_padding'],
                    'installation_and_activation_api_bytes': fee})
    with (out / 'measurements.csv').open('w', newline='') as channel:
        writer = csv.DictWriter(channel, fieldnames=list(statistics[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(statistics)
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.9))
    x = np.arange(3)
    for index, summary in enumerate(summaries):
        for capacity in (1, 2):
            low, high = [], []
            for scenario in ('normal', 'delay', 'loss'):
                case = next(r for r in summary['cases'] if r['capacity'] == capacity and r['scenario'] == scenario and r['family'] == 'tt')
                task = case['age']['tasks'][2]
                low.append(task['violation_lower'] * 100)
                high.append(task['violation_upper'] * 100)
            offset = (-.18 if capacity == 1 else .06) + index * .11
            color = '#333333' if capacity == 1 else '#b8781b'
            label = f'K={capacity}, ' + ('Diag.' if index else 'Ord.')
            axes[0].vlines(x + offset, low, high, color=color, linewidth=1.3)
            axes[0].scatter(x + offset, high, s=20, color=color, marker='s' if index else 'o',
                            facecolors='none' if index else color, label=label)
    axes[0].set_xticks(x, ['Normal', 'Delay', 'Loss'])
    axes[0].set_ylabel('Shared task deadline violation (%)', fontproperties=font)
    axes[0].set_title('Received age bounds, fixed TT configuration', fontproperties=font)
    axes[0].legend(prop=small, frameon=False, ncol=2)
    labels, received, missing = [], [], []
    for capacity in (1, 2):
        for scenario in ('normal', 'delay', 'loss'):
            case = next(r for r in summaries[0]['cases'] if r['capacity'] == capacity and r['scenario'] == scenario and r['family'] == 'tt')
            rows = [json.loads(line) for line in (args.ordinary / case['name'] / 'wire-observations.jsonl').read_text().splitlines()]
            lost = set(case['export_delivery']['missing_packet_ids'])
            loss_bytes = sum(max(60, r['frame_bytes']) for r in rows if r['node'] == 4 and r['port'] == 3 and r['packet_id'] in lost)
            missing.append(loss_bytes)
            received.append(case['measured_cost']['export_padded_bytes'] - loss_bytes)
            labels.append(f'K={capacity}\n' + {'normal': 'N', 'delay': 'D', 'loss': 'L'}[scenario])
    ticks = np.arange(6)
    axes[1].bar(ticks, received, width=.65, color='#bcbcbc', edgecolor='#444444', linewidth=.5, label='Received')
    axes[1].bar(ticks, missing, bottom=received, width=.65, color='#555555', edgecolor='#444444', linewidth=.5, label='Lost after export')
    axes[1].set_xticks(ticks, labels)
    axes[1].set_ylabel('Charged export bytes, minimum padding (B)', fontproperties=font)
    axes[1].set_title('Sink export, ordinary TT configuration', fontproperties=font)
    axes[1].legend(prop=small, frameon=False)
    for axis in axes:
        axis.grid(axis='y', color='#dddddd', linewidth=.4)
        axis.set_axisbelow(True)
        for label in axis.get_xticklabels() + axis.get_yticklabels():
            label.set_fontproperties(small)
    fig.text(.5, .025, 'One execution per setting. Intervals are causal bounds, not confidence intervals. N: normal, D: delay, L: loss.',
             ha='center', fontproperties=small)
    fig.tight_layout(rect=(0, .06, 1, 1), pad=.8)
    name = 'received-age-and-export-cost'
    fig.savefig(out / (name + '.png'), dpi=300, metadata={'Software': 'Matplotlib'})
    fig.savefig(out / (name + '.pdf'), metadata={'Author': '', 'Creator': '', 'Title': name})
    plt.close(fig)
    archive = out / 'executed-source/experiments'
    archive.mkdir(parents=True)
    shutil.copyfile(__file__, archive / Path(__file__).name)
    manifest = {'input_sha256': hashes, 'plot_source_sha256': sha(Path(__file__)), 'font_file': str(font_path),
                'font_sha256': sha(font_path), 'dpi': 300, 'scope': '软件正确性核对图，无方法排名或统计显著性',
                'artifact_sha256': {p.name: sha(p) for p in out.glob('*') if p.is_file()}}
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
