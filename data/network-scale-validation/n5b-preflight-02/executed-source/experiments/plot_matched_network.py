"""从开发网络试点的真实调用和决策记录绘制软件执行时间。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager


LABELS = {'static': 'Model-tuned static', 'feedback': 'Uniform feedback', 'local': 'Uniform local',
          'joint': 'Uniform joint', 'phase_joint': 'Temporal joint', 'uniform_guard': 'Uniform repair',
          'phase_guard': 'Temporal repair', 'phase_local_guard': 'Temporal local repair'}
PROFILES = {'stable-high': 'Stable / high', 'shared_priority-high': 'Priority / high',
            'switch-low': 'Notified switch / low', 'bursty_jitter-low': 'Jitter / low'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.analysis / 'manifest.json').read_text())
    if manifest['status'] != 'passed' or len(manifest['cases']) != 32 or manifest['seed'] != 11:
        raise ValueError('图源必须是完整的开发试点')
    for name in ('timing.csv', 'rpc-operations.jsonl'):
        if sha(args.analysis / name) != manifest['artifact_sha256'][name]:
            raise ValueError('图源摘要不一致')
    with (args.analysis / 'timing.csv').open() as stream:
        timing = list(csv.DictReader(stream))
    rpc = [json.loads(line) for line in (args.analysis / 'rpc-operations.jsonl').read_text().splitlines()]
    points = []
    for case in manifest['cases']:
        solver = [float(row['solver_ms']) for row in timing if row['case'] == case['name']]
        calls = rpc[case['rpc_start_index']:case['rpc_stop_index']]
        durations = [(row['after_monotonic_ns'] - row['before_monotonic_ns']) / 1e6
                     for row in calls if row['kind'] in ('update', 'online_observation')]
        if len(solver) != 10 or (case['method'] == 'static' and durations):
            raise ValueError('决策分母或静态调用不符')
        for kind, values in (('solver', solver), ('rpc', durations)):
            points.append({'case': case['name'], 'method': case['method'],
                           'profile': case['scenario'] + '-' + case['budget_level'], 'kind': kind,
                           'count': len(values), 'mean_ms': statistics.mean(values) if values else None,
                           'min_ms': min(values) if values else None, 'max_ms': max(values) if values else None})
    font_path = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(font_path))
    font = font_manager.FontProperties(fname=font_path)
    if font.get_name() != 'Times New Roman' or Path(font_manager.findfont(
            font_manager.FontProperties(family='Times New Roman'), fallback_to_default=False)).resolve() != font_path.resolve():
        raise ValueError('Times New Roman 字体解析错误')
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 9,
                         'pdf.fonttype': 42, 'axes.unicode_minus': False})
    args.output.mkdir(parents=True, exist_ok=False)
    fig, axes = plt.subplots(1, 2, figsize=(7.8, 4.9), sharey=True)
    colors, symbols = ('#333333', '#27749c', '#ad6824', '#588443'), ('o', 's', '^', 'D')
    for col, kind in enumerate(('solver', 'rpc')):
        axis = axes[col]
        for at, profile in enumerate(PROFILES):
            for index, method in enumerate(manifest['methods']):
                point = next(p for p in points if (p['method'], p['profile'], p['kind']) == (method, profile, kind))
                if not point['count']:
                    continue
                x, y = point['mean_ms'], index + (at - 1.5) * .15
                axis.errorbar(x, y, xerr=[[x - point['min_ms']], [point['max_ms'] - x]],
                              fmt=symbols[at], color=colors[at], markersize=3.3,
                              elinewidth=.7, capsize=1.5,
                              label=PROFILES[profile] if index == (0 if kind == 'solver' else 1) else None)
        axis.set_yticks(range(8), [LABELS[m] for m in manifest['methods']])
        axis.set_title('Solver execution' if kind == 'solver' else 'Completed online RPC')
        axis.set_xlabel('Elapsed time (ms)')
        axis.set_xlim(-1.5, 75) if col == 0 else axis.set_xlim(-.05, 2.65)
        axis.grid(axis='x', color='#dedede', linewidth=.4)
        axis.set_axisbelow(True)
    axes[0].invert_yaxis()
    axes[1].text(.3, 0, 'No online RPC', va='center', fontsize=8)
    axes[1].axvline(.1, color='#888888', linestyle='--', linewidth=.7)
    axes[1].text(.13, -.6, 'Model: 0.1 ms', color='#555555', fontsize=8)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', ncol=2, frameon=False, fontsize=8)
    fig.text(.5, .055, 'BMv2 development pilot; one consumed training seed, four profiles.', ha='center', fontsize=8)
    fig.text(.5, .02, 'Points: means. Bars: observed ranges within each case. Software execution times.', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .105, 1, .88), pad=.7)
    name = 'matched-network-timing'
    fig.savefig(args.output / f'{name}.png', dpi=300, metadata={'Software': 'Matplotlib'})
    fig.savefig(args.output / f'{name}.pdf', metadata={'Author': '', 'Creator': '', 'Title': 'BMv2 development pilot timing'})
    plt.close(fig)
    fonts = subprocess.run(['pdffonts', str(args.output / f'{name}.pdf')], check=True, text=True, capture_output=True).stdout
    if not fonts.splitlines()[2:] or any('TimesNewRoman' not in line or ' yes ' not in line for line in fonts.splitlines()[2:]):
        raise ValueError('PDF 未嵌入指定字体')
    (args.output / f'{name}-fonts.txt').write_text(fonts)
    with (args.output / 'plot-data.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(points[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(points)
    source = Path(__file__).resolve()
    target = args.output / 'executed-source/experiments' / source.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    record = {'status': 'passed', 'analysis_manifest_sha256': sha(args.analysis / 'manifest.json'),
              'input_sha256': {n: sha(args.analysis / n) for n in ('timing.csv', 'rpc-operations.jsonl')},
              'source_sha256': {'experiments/' + source.name: sha(source)},
              'font_name': font.get_name(), 'font_file': str(font_path), 'font_sha256': sha(font_path), 'dpi': 300,
              'scope': '四开发场景各一次运行的均值与观察范围，非统计置信区间、性能排名或硬件时延',
              'artifact_sha256': {str(p.relative_to(args.output)): sha(p) for p in args.output.rglob('*') if p.is_file()}}
    (args.output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
