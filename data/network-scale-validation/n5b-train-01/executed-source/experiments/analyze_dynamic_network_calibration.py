"""有限动态网络校准的实际费用、完整年龄和全局参数选择。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys

from check_matched_network import charges_for, direct_window, load, rows, sha
from check_dynamic_network_calibration import audit
from dynamic_calibration_summary import summarize
from dynamic_calibration_contract import select_parameters, upper_rates, normalized_excess
from check_dynamic_calibration_analysis import check_summary
from check_rolling_reference import near, same
from network_entry_bounds import costs, generation_bounds, output_frames

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


from analyze_network_static import check_cache_and_budget

def analyze(run, output):
    verification = audit(run)
    m, rpc = load(run / 'manifest.json'), rows(run / 'rpc-operations.jsonl')
    output.mkdir(parents=True, exist_ok=False)
    records = output_frames(run, m['cases'])
    cache_checks = sum(check_cache_and_budget(run / c['name']) for c in m['cases'])
    totals, table, timing = [], [], []
    for c in m['cases']:
        directory, target = run / c['name'], output / c['name']
        target.mkdir()
        for p in directory.iterdir():
            if p.suffix in ('.json', '.jsonl', '.pcap'):
                shutil.copyfile(p, target / p.name)
        charges = charges_for(directory, rpc)
        cost = costs(directory, c, records[c['name']], charges)
        age = generation_bounds(directory, c)
        write(target / 'window-costs.json', cost)
        write(target / 'generation-bounds.json', age)
        with (target / 'output-frames.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[c['name']][0]), lineterminator='\n')
            writer.writeheader()
            writer.writerows(records[c['name']])
        decisions = rows(directory / 'decisions.jsonl')
        solver_ms = [(d['solver_finished_ns'] - d['solver_started_ns']) / 1e6 for d in decisions if 'selected' in d]
        lateness_ms = [(d['cycle_started_ns'] - c['origin_monotonic_ns'] - d['scheduled_offset_us'] * 1000) / 1e6 for d in decisions]
        own = [r for r in rpc[c['rpc_start_index']:c['rpc_stop_index']] if r['kind'] in ('update', 'online_observation')]
        rpc_ms = [(r['after_monotonic_ns'] - r['before_monotonic_ns']) / 1e6 for r in own]
        send_late = [r['schedule_lateness_ns'] / 1e6 for r in rows(directory / 'send.jsonl')]
        notices = rows(directory / 'notices.jsonl')
        schedule = load(directory / 'notice-schedule.json')
        notice_late = [(n['received_ns'] - c['origin_monotonic_ns'] - next(s['offset_us'] for s in schedule if s['message']['version'] == n['message']['version']) * 1000) / 1e6 for n in notices]
        entry = {'case': c['name'], 'scenario': c['scenario'], 'budget_level': c['budget_level'], 'method': c['method'],
                 'seed': c['seed'], 'parameter_index': c['parameter_index'], 'position': c['position'], 'packets': c['packet_count'], 'reports': c['report_count'], 'successful_updates': c['successful_updates'],
                 'loss_lower': age['loss_lower'], 'loss_upper': age['loss_upper'],
                 'active_interval_coverage': age['weighted_active_interval_coverage'],
                 'window_feasibility': cost['window_feasibility'], 'wall_clock_compatible': cost['wall_clock']['compatible'],
                 'api_bytes_per_s': cost['api_bytes_per_s'], 'local_notice_bytes_per_s': cost['local_notice_bytes_per_s'],
                 'solver_mean_ms': statistics.mean(solver_ms) if solver_ms else None,
                 'solver_max_ms': max(solver_ms) if solver_ms else None, 'rpc_mean_ms': statistics.mean(rpc_ms) if rpc_ms else None,
                 'rpc_max_ms': max(rpc_ms) if rpc_ms else None, 'cycle_max_lateness_ms': max(lateness_ms),
                 'sender_max_lateness_ms': max(send_late), 'notice_max_lateness_ms': max(notice_late) if notice_late else None,
                 'skipped_epochs': sum(d.get('skipped', False) for d in decisions)}
        for k in ('e1', 'e2', 'e3', 'export'):
            for bound in ('lower', 'upper'):
                values = cost[f'measurement_{bound}_bytes']
                entry[f'{k}_{bound}_bytes_per_s'] = values[k] / 8 if values is not None else None
        entry['normalized_excess_upper'] = normalized_excess(upper_rates(cost), c['scenario'], c['budget_level'])
        table.append(entry)
        totals.append({'name': c['name'], 'conditioned_cost': cost, 'loss_bounds': [age['loss_lower'], age['loss_upper']],
                       'generation_clock_compatible': all(v['compatible'] is not False for v in age['local_clock_offset'].values())})
        for d in decisions:
            if 'selected' in d:
                timing.append({'case': c['name'], 'method': c['method'], 'scheduled_offset_us': d['scheduled_offset_us'],
                    'actual_cutoff_us': d['time_us'], 'solver_ms': (d['solver_finished_ns'] - d['solver_started_ns']) / 1e6,
                    'cycle_ms': (d['cycle_finished_ns'] - d['cycle_started_ns']) / 1e6,
                    'planned_api_bytes': None if d['selected']['forecast'] is None else sum(d['selected']['forecast']['cost']['control_serialized_bytes_by_kind'].values()),
                    'actual_cycle_api_bytes': sum(r['bytes'] for r in charges if r['layer'] == 'api_serialized' and
                          d['cycle_started_ns'] - c['origin_monotonic_ns'] <= r['time_ns'] <= d['cycle_finished_ns'] - c['origin_monotonic_ns']),
                    'actual_write_rpcs': len(d['writes']), 'actual_read_rpcs': len(d['reads'])})
    for name, rs in (('summary.csv', table), ('timing.csv', timing)):
        with (output / name).open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rs[0]), lineterminator='\n')
            writer.writeheader()
            writer.writerows(rs)
    verification.update(cache_and_budget_checks=cache_checks, output_frames=sum(map(len, records.values())))
    write(output / 'checks.json', verification)
    for folder in run.glob('node-*'):
        target = output / folder.name
        target.mkdir()
        for p in folder.glob('*_out.pcap'):
            shutil.copyfile(p, target / p.name)
    # 原编译产物的 source_info 含本机绝对路径，仅在原始目录保留；摘要不改变。
    for name in ('rpc-operations.jsonl', 'rpc.jsonl', 'rpc.pcap', 'unit-tests.txt', 'frozen-inputs.json', 'calibration-plan.json'):
        shutil.copyfile(run / name, output / name)
    shutil.copyfile(run / 'execution-order.json', output / 'execution-order.json')
    control_frames = rows(run / 'rpc.jsonl')
    if any(r['socket_drop_counter'] for r in control_frames):
        raise ValueError('控制捕获套接字丢包')
    shutil.copytree(run / 'executed-source', output / 'executed-source')
    sources = ('experiments/analyze_network_static.py', 'experiments/static_summary.py',
               'experiments/network_static_contract.py', 'experiments/check_static_analysis.py',
               'experiments/check_network_static.py', 'tests/test_network_static.py',
               'experiments/analyze_network_calibration.py', 'experiments/calibration_summary.py',
               'experiments/check_network_calibration.py', 'experiments/check_calibration_analysis.py', 'tests/test_network_calibration.py', 'experiments/network_entry_bounds.py',
               'experiments/check_matched_network.py', 'experiments/check_into_validation.py',
               'experiments/check_adaptive_reference.py', 'experiments/check_rolling_reference.py', 'tests/test_network_entry_bounds.py')
    sources += ('experiments/analyze_static_confirmation.py', 'experiments/check_confirmation_analysis.py',
                'experiments/check_static_confirmation.py', 'experiments/confirmation_contract.py',
                'experiments/confirmation_repeats.py', 'experiments/diagnose_static_transfer.py',
                'tests/test_static_confirmation.py')
    sources += ('experiments/analyze_dynamic_network_calibration.py', 'experiments/check_dynamic_calibration_analysis.py',
                'experiments/check_dynamic_network_calibration.py', 'experiments/dynamic_calibration_contract.py',
                'experiments/dynamic_calibration_summary.py', 'tests/test_dynamic_network_calibration.py')
    for name in sources:
        target = output / 'executed-analysis-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    regression = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=ROOT,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (output / 'final-unit-tests.txt').write_text(regression.stdout)
    if regression.returncode:
        raise ValueError('最终回归失败')
    findings = {'cases': len(table), 'packets': verification['packets'], 'reports': verification['reports'],
                'output_frames': verification['output_frames'], 'decisions': verification['decisions'],
                'successful_updates': sum(c['successful_updates'] for c in m['cases']),
                'skipped_epochs': sum(r['skipped_epochs'] for r in table),
                'window_feasibility': dict(__import__('collections').Counter(r['window_feasibility'] for r in table)),
                'wall_clock_compatible_cases': sum(r['wall_clock_compatible'] for r in table),
                'generation_clock_compatible_cases': sum(r['generation_clock_compatible'] for r in totals),
                'control_captured_frames': len(control_frames), 'control_captured_bytes': sum(r['frame_bytes'] for r in control_frames),
                'scope': '184 例固定参数族网络校准，1301/1423 已消费；不是独立测试排名或置信区间'}
    findings.update(summarize(run, output, m['cases'], table))
    scores, selections = select_parameters(load(run / 'calibration-plan.json'), table)
    write(output / 'parameter-scores.json', scores)
    write(output / 'frozen-parameters.json', selections)
    findings['feasible_parameters'] = {r['method']: r['feasible_parameters'] for r in selections}
    findings['selected_parameters'] = {r['method']: r['selected_parameter'] for r in selections}
    chosen = {r['method']: r['selected_parameter'] for r in selections if r['status'] != 'unavailable'}
    selected_rows = [r for r in table if r['parameter_index'] is None or r['parameter_index'] == chosen.get(r['method'], -1)]
    write(output / 'selected-calibration-cases.json', selected_rows)
    findings['selected_case_count'] = len(selected_rows)
    write(output / 'summary-checks.json', check_summary(output, m['cases']))
    write(output / 'findings.json', findings)
    record = dict(m, concurrent_activity='主矩阵期间没有同宿主回归、离线分析或第二组网络；逐案例保留负载与进程观察，不保证完全排除外部干扰',
                  raw_manifest_sha256=sha(run / 'manifest.json'), raw_artifact_sha256=m['artifact_sha256'],
                  analysis_source_sha256={n: sha(ROOT / n) for n in sources}, findings=findings,
                  unit_regression={'exit_code': regression.returncode, 'log': 'final-unit-tests.txt'},
                  artifact_sha256={str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()
                                   and 'executed-source' not in p.parts and 'executed-analysis-source' not in p.parts})
    write(output / 'manifest.json', record)
    print(json.dumps(findings, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    analyze(a.run, a.output)
