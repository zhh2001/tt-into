#!/usr/bin/env python3
"""生成可追溯的合成事件预实验，不作为 BMv2 编排测量。"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import sys
import time
import numpy
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from orchestration.model import Task, Packet, validate_trace
from orchestration.metrics import evaluate
from orchestration.replay import POLICIES, replay
from orchestration.oracle import solve

THRESHOLDS = [1, 50000, 100000, 200000, 400000]
SEEDS = list(range(20))
NODE_THRESHOLDS = [1, 200000, 400000, 600000]


def scenarios():
    period, end = 200000, 1200000
    tasks = [Task(1, 250000, 3, initial_generated_us=-1000),
             Task(2, 450000, 2, initial_generated_us=-1000),
             Task(3, 650000, 1, initial_generated_us=-1000)]
    for name, phase, delay in [('aligned', 0, 1000), ('staggered', 100000, 1000),
                                ('slow_export', 100000, 300000), ('header_budget_shortfall', 100000, 1000)]:
        inputs = sorted((t + (phase if flow == 2 else 0), flow) for t in range(0, end, period) for flow in [1, 2])
        packets = [Packet(index + 1, flow, (1, 2, 3), (t, t + 100, t + 200),
                          (t + 50, t + 150, t + 250), t + delay, capacity=1)
                   for index, (t, flow) in enumerate(inputs)]
        # 所有轨迹流量的总量预算，包含窗口末端后送达的在途报告。
        budgets = [len(packets) * 28] * 3 + [len(packets) * 62]
        if name == 'header_budget_shortfall':
            budgets[0] = len(packets) * 8 - 1
        yield name, tasks, packets, 0, end, budgets


def within(cost, budgets):
    return all(actual <= budget for actual, budget in zip(cost['inband_per_link_bytes'] + [cost['export_bytes']], budgets))


def plot(summary, directory):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties, findfont
    font_path = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    if not font_path.exists() or Path(findfont(FontProperties(fname=str(font_path)))).resolve() != font_path:
        raise RuntimeError('Times New Roman 字体未正确解析')
    font = FontProperties(fname=str(font_path), size=10)
    plt.rcParams.update({'pdf.fonttype': 42})
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 3.0), sharey=True)
    labels = ['TT(flow)', 'Port(shared)', 'Uniform', 'Weighted', 'Rotation', 'Static']
    for axis, name in zip(axes, ['aligned', 'staggered']):
        best = summary['scenarios'][name]['best_reference_configurations']
        values = [best[policy]['mean_weighted_violation'] for policy in POLICIES]
        axis.bar(range(6), values, color='#446b8b', edgecolor='#243d51')
        axis.set_xticks(range(6), labels, rotation=25, ha='right')
        axis.set_ylim(0, 1)
        axis.set_title(name.capitalize() + ' opportunities', fontproperties=font)
        axis.set_ylabel('Weighted violation fraction', fontproperties=font)
        for label in axis.get_xticklabels() + axis.get_yticklabels():
            label.set_fontproperties(font)
        for i, value in enumerate(values):
            axis.text(i, value + 0.015, f'{value:.3f}', ha='center', fontproperties=font)
        axis.spines[['top', 'right']].set_visible(False)
    fig.tight_layout()
    directory.mkdir(parents=True, exist_ok=True)
    fig.savefig(directory / 'reference-baselines.png', dpi=300, metadata={'Software': 'Matplotlib'})
    fig.savefig(directory / 'reference-baselines.pdf', metadata={'Title': 'Synthetic event reference checks', 'Author': '', 'Creator': ''})
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--figures', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = ['src/orchestration/model.py', 'src/orchestration/metrics.py', 'src/orchestration/replay.py',
               'src/orchestration/oracle.py', 'experiments/run_orchestration_reference.py']
    summary = {'run_id': output.name, 'start_utc': datetime.now(timezone.utc).isoformat(),
               'status': 'running', 'kind': 'synthetic_event_reference',
               'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
               'versions': {'python': platform.python_version(), 'numpy': numpy.__version__, 'scipy': scipy.__version__},
               'start_load_average': list(os.getloadavg()), 'threshold_grid_us': THRESHOLDS,
               'per_node_threshold_grid_us': NODE_THRESHOLDS, 'reservoir_seeds': SEEDS,
               'format': {'header_bytes': 8, 'record_bytes': 20, 'export_fixed_bytes': 42},
               'prehistory': '共同给定初始已收样本，生成于 -1000 us；窗口门控初始无成功状态，这是输入假设而非实测预热',
               'budget_window': '整个输入轨迹及其报告总量，年龄窗口单独给出，含窗口后到达报告费用',
               'control_scope': '静态规则窗口内不更新，控制为零；初始安装及探针成本未包含，不能推断部署总成本',
               'tuning': '所有对照在同一已知轨迹枚举统一及逐节点阈值，静态流授权另枚举非空节点子集，属于乐观对照参照',
               'reservoir_scope': '理想实数随机优先级，只实现事件参考，尚未移植为 P4 有限精度随机选择',
               'scenarios': {}}
    path = output / 'summary.json'
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    started = time.monotonic()
    try:
        for name, tasks, packets, start, end, budgets in scenarios():
            validate_trace(tasks, packets, start, end)
            directory = output / name
            directory.mkdir()
            inputs = {'tasks': [asdict(task) for task in tasks], 'packets': [asdict(p) for p in packets],
                      'age_window_us': [start, end], 'budgets_per_link_bytes': budgets}
            (directory / 'input.json').write_text(json.dumps(inputs, ensure_ascii=False, indent=2) + '\n')
            records, best = [], {}
            for policy in POLICIES:
                configurations = []
                threshold_options = list(THRESHOLDS)
                threshold_options += [dict(zip([1, 2, 3], values)) for values in itertools.product(NODE_THRESHOLDS, repeat=3)]
                subsets = [list(nodes) for size in range(1, 4) for nodes in itertools.combinations([1, 2, 3], size)]
                for threshold in threshold_options:
                    assignments = [{1: a, 2: b} for a, b in itertools.product(subsets, repeat=2)] if policy == 'static_flow' else [None]
                    for assignment in assignments:
                        trials = []
                        for seed in (SEEDS if 'reservoir' in policy else [0]):
                            replayed = replay(tasks, packets, threshold, policy, seed, assignment)
                            metrics = evaluate(tasks, packets, replayed['selected'], start, end)
                            row = {'policy': policy, 'threshold_us': threshold, 'seed': seed,
                                   'static_assignment': assignment, 'selected': replayed['selected'],
                                   'cost': replayed['cost'], 'metrics': metrics,
                                   'within_budget': within(replayed['cost'], budgets)}
                            records.append(row)
                            trials.append(row)
                        configurations.append({'threshold_us': threshold, 'static_assignment': assignment,
                                               'seed_count': len(trials),
                                               'mean_weighted_violation': sum(t['metrics']['weighted_violation_fraction'] for t in trials) / len(trials),
                                               'min_weighted_violation': min(t['metrics']['weighted_violation_fraction'] for t in trials),
                                               'max_weighted_violation': max(t['metrics']['weighted_violation_fraction'] for t in trials),
                                               'mean_fulfilled_tasks': sum(t['metrics']['fulfilled_task_count'] for t in trials) / len(trials),
                                               'mean_total_byte_hops': sum(t['cost']['total_byte_hops'] for t in trials) / len(trials),
                                               'all_within_budget': all(t['within_budget'] for t in trials)})
                feasible = [c for c in configurations if c['all_within_budget']]
                best[policy] = min(feasible, key=lambda c: (c['mean_weighted_violation'], c['mean_total_byte_hops'])) if feasible else None
            (directory / 'baseline-trials.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records))
            oracles = []
            for threshold in THRESHOLDS:
                result = solve(tasks, packets, threshold, start, end, budgets)
                oracles.append(result)
            (directory / 'oracles.json').write_text(json.dumps(oracles, ensure_ascii=False, indent=2) + '\n')
            summary['scenarios'][name] = {'input_packets': len(packets), 'baseline_trials': len(records),
                                         'best_reference_configurations': best,
                                         'oracle_statuses': [r['status'] for r in oracles],
                                         'oracle_admitted_weights': [r.get('admitted_weight') for r in oracles]}
            path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
            print(name, 'oracle', summary['scenarios'][name]['oracle_admitted_weights'], flush=True)
        summary['status'] = 'passed'
        if args.figures:
            plot(summary, args.figures)
    except Exception as exc:
        summary.update(status='failed', failure=str(exc))
        raise
    finally:
        summary['finish_utc'] = datetime.now(timezone.utc).isoformat()
        summary['wall_seconds'] = time.monotonic() - started
        summary['finish_load_average'] = list(os.getloadavg())
        summary['artifact_sha256'] = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob('*') if p.is_file() and p.name != 'summary.json'}
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
