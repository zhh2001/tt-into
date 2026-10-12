"""由普通版实测报告和逐字段确认生成正确性时间线。"""
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
from check_into_validation import load, rows

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = load(args.run / 'ordinary/manifest.json')
    font_path = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(font_path))
    font = font_manager.FontProperties(fname=font_path, size=9)
    small = font_manager.FontProperties(fname=font_path, size=8)
    if font.get_name() != 'Times New Roman' or Path(font_manager.findfont(font, fallback_to_default=False)).resolve() != font_path.resolve():
        raise ValueError('字体解析失败')
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 8, 'pdf.fonttype': 42, 'axes.unicode_minus': False})
    args.output.mkdir(parents=True, exist_ok=False)
    figure, axes = plt.subplots(2, 2, figsize=(7.8, 4.5), sharex='col')
    colors = ['#356e99', '#a47721', '#5d834f']
    records = []
    rpc = rows(args.run / 'ordinary/rpc-operations.jsonl')
    for column, name in enumerate(('partial_failure', 'causal_loop')):
        case = next(c for c in manifest['cases'] if c['name'] == name)
        directory = args.run / 'ordinary' / name
        origin = case['origin_monotonic_ns']
        actions = rows(directory / 'actions.jsonl')
        reports = [r for r in rows(directory / 'collector.jsonl') if r.get('ether_type') == 0x88b6 and r['session'] == 20]
        for node in (1, 2, 3):
            stamps = [(r['receive_monotonic_ns'] - origin) / 1e9 for r in reports if any(x['switch_id'] == node for x in r['records'])]
            axes[0, column].scatter(stamps, [node] * len(stamps), marker='|', s=40, linewidths=.8, color=colors[node - 1], label=f'Object {node}')
            records += [{'case': name, 'kind': 'sample', 'key': str(node), 'time_s': t, 'value': node} for t in stamps]
        for j, (node, tag) in enumerate(((1, 1001), (3, 1001), (3, 2002))):
            relevant = [a for a in actions if a['field'] == 'enabled' and a['node'] == node and a['flow']['tag'] == tag]
            events = [(0.0, float(relevant[0]['value']))]
            for a in relevant[1:]:
                time_s = ((a['confirmation_available_ns'] if a['success'] else rpc[a['rpc_index']]['after_monotonic_ns']) - origin) / 1e9
                events.append((time_s, float(a['value']) if a['success'] else float('nan')))
            for r in load(directory / 'reconciliation.json') if (directory / 'reconciliation.json').exists() else []:
                if r['field'] == 'enabled' and r['node'] == node and r['flow']['tag'] == tag:
                    events.append(((r['received_ns'] - origin) / 1e9, float(r['value'])))
            events.sort()
            events.append((case['duration_us'] / 1e6, events[-1][1]))
            x, y = zip(*events)
            axes[1, column].step(x, y, where='post', color=colors[j], linestyle=('-', '--', ':')[j],
                                 linewidth=1.0, label=f'x({node}, {tag})')
            records += [{'case': name, 'kind': 'permission', 'key': f'{node}:{tag}', 'time_s': t,
                         'value': v if np.isfinite(v) else 'unknown'} for t, v in events]
        if name == 'partial_failure':
            failed = next(a for a in actions if not a['success'])
            start = (rpc[failed['rpc_index']]['after_monotonic_ns'] - origin) / 1e9
            end = (load(directory / 'reconciliation.json')[0]['received_ns'] - origin) / 1e9
            for axis in axes[:, column]:
                axis.axvspan(start, end, color='#dddddd', alpha=.6, zorder=0)
            axes[0, column].text((start + end) / 2, 3.4, 'Unknown', ha='center', fontproperties=small)
        else:
            n = rows(directory / 'notices.jsonl')[-1]
            stamp = (n['received_ns'] - origin) / 1e9
            for axis in axes[:, column]:
                axis.axvline(stamp, color='#555555', linewidth=.7, linestyle='--')
            axes[0, column].text(stamp + .12, 3.4, 'Demand for object 3', fontproperties=small)
            records.append({'case': name, 'kind': 'notice', 'key': str(n['message']['version']), 'time_s': stamp, 'value': 3})
        axes[0, column].set_title('Partial write failure' if column == 0 else 'Causal orchestration', fontproperties=font)
        axes[0, column].set_yticks([1, 2, 3])
        axes[0, column].set_ylim(.6, 3.7)
        axes[1, column].set_ylim(-.12, 1.12)
        axes[1, column].set_yticks([0, 1])
        axes[1, column].set_xlabel('Time since traffic start (s)', fontproperties=font)
        for axis in axes[:, column]:
            axis.set_xlim(0, case['duration_us'] / 1e6)
            axis.grid(axis='y', color='#e0e0e0', linewidth=.5)
            for tick in axis.get_xticklabels() + axis.get_yticklabels():
                tick.set_fontproperties(small)
    axes[0, 0].set_ylabel('Received sample object', fontproperties=font)
    axes[1, 0].set_ylabel('Confirmed permission', fontproperties=font)
    handles, labels = axes[1, 0].get_legend_handles_labels()
    figure.legend(handles, labels, prop=small, frameon=False, loc='upper center', ncol=3)
    figure.text(.5, .025, 'Ordinary BMv2 correctness run. Marks: actual receipt. Gray span: unresolved configuration.',
                ha='center', fontproperties=small)
    figure.tight_layout(rect=(0, .075, 1, .91))
    name = 'live-identity-and-transitions'
    figure.savefig(args.output / f'{name}.png', dpi=300, metadata={'Software': 'Matplotlib'})
    figure.savefig(args.output / f'{name}.pdf', metadata={'Author': '', 'Creator': '', 'Title': 'Live configuration correctness'})
    plt.close(figure)
    fonts = subprocess.check_output(['pdffonts', str(args.output / f'{name}.pdf')], text=True)
    if not fonts.splitlines()[2:] or any('TimesNewRoman' not in line or ' yes ' not in line for line in fonts.splitlines()[2:]):
        raise ValueError('PDF 字体未正确嵌入')
    (args.output / 'fonts.txt').write_text(fonts)
    with (args.output / 'plot-data.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(records)
    sources = ('experiments/plot_into_validation.py', 'experiments/check_into_validation.py',
               'experiments/check_adaptive_reference.py', 'experiments/check_rolling_reference.py')
    for source in sources:
        p = args.output / 'executed-source' / source
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, p)
    record = {'input_manifest_sha256': hashlib.sha256((args.run / 'manifest.json').read_bytes()).hexdigest(),
              'source_sha256': {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in sources},
              'font_name': font.get_name(), 'font_file': str(font_path), 'font_sha256': hashlib.sha256(font_path.read_bytes()).hexdigest(),
              'dpi': 300, 'scope': '实际报告与本地逐字段确认，非同步年龄或性能比较',
              'artifact_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.iterdir() if p.is_file()}}
    (args.output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'plot_rows': len(records), 'font': font.get_name()}))


if __name__ == '__main__':
    main()
