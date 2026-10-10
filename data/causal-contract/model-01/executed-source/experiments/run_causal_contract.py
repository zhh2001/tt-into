"""穷举小规模信息边界与需求通知提前量，输出执行源码和输入。"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from itertools import product
import json
import os
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.orchestration.causal_contract import (  # noqa: E402
    Opportunity, deployment_lead_certificate, two_opportunity_frontier,
)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def execute(output):
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    source_names = ['src/orchestration/causal_contract.py',
                    'experiments/run_causal_contract.py',
                    'experiments/check_causal_contract.py', 'tests/test_causal_contract.py']
    source_hashes = {}
    for name in source_names:
        raw = (ROOT / name).read_bytes()
        target = output / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        source_hashes[name] = hashlib.sha256(raw).hexdigest()
    manifest = {'schema': 1, 'kind': 'finite_event_model', 'status': 'running',
                'started_utc': datetime.now(timezone.utc).isoformat(),
                'source_sha256': source_hashes, 'python': platform.python_version(),
                'system': platform.system(), 'kernel': platform.release(),
                'cpu_count': os.cpu_count(), 'load_average': list(os.getloadavg()),
                'randomness': 'none', 'git_commit_required': False,
                'measurement': 'ideal_integer_microseconds',
                'cost_unit': 'record_count; report_attempt_count; branch_hint_assumption'}
    write(output / 'manifest.json', manifest)
    try:
        spec = {
            'tasks': [1, 2], 'initial_generated_us': [-100000, -100000],
            'first': {'generated_us': 0, 'eligible': [1, 2]},
            'futures': [{'generated_us': 100000, 'eligible': [1]},
                        {'generated_us': 100000, 'eligible': [2]}],
            'start_us': 0, 'end_us': 200000, 'capacities': [1, 2],
            'record_budgets': [1, 2, 3], 'deadlines_us': [150000, 200000, 250000, 300000],
            'report_delays_us': [0, 20000, 60000], 'branch_hints': [False, True],
            'heterogeneous_deadlines_us': [[200000, 250000], [250000, 200000]],
            'hint_cost': '单独的信息假设；未给出获取机制及物理字节费用',
            'lead_grid': {'advance_us': [0, 20000, 40000, 60000, 100000],
                          'install_delays_us': [0, 20000, 60000, 100000],
                          'report_delays_us': [0, 20000, 60000],
                          'packet_times_us': list(range(100000, 300000, 20000)),
                          'initial_generated_us': 0, 'deadline_us': 150000,
                          'change_us': 100000, 'end_us': 300000},
            'assumptions': ['fixed_eligibility', 'two_opportunities', 'uniform_task_weight',
                            'fixed_known_report_delay', 'no_loss', 'ideal_clock',
                            'arbitrary_current_packet_selection', 'no_network_execution'],
            'budget_exclusions': ['bytes', 'fixed_headers', 'control_rpc', 'hint_transport'],
        }
        write(output / 'specification.json', spec)
        inputs = []
        for capacity, budget, deadline, delay, hint in product(
                spec['capacities'], spec['record_budgets'], spec['deadlines_us'],
                spec['report_delays_us'], spec['branch_hints']):
            inputs.append({'group': 'grid', 'capacity': capacity, 'record_budget': budget,
                           'deadlines_us': [deadline, deadline],
                           'report_delay_us': delay, 'branch_hint': hint})
        for deadlines, hint in product(spec['heterogeneous_deadlines_us'], spec['branch_hints']):
            inputs.append({'group': 'heterogeneous', 'capacity': 1, 'record_budget': 2,
                           'deadlines_us': deadlines, 'report_delay_us': 0, 'branch_hint': hint})
        results = []
        for index, inputs_row in enumerate(inputs):
            result = two_opportunity_frontier(
                tasks=(1, 2), initial={1: -100000, 2: -100000},
                deadlines=dict(zip((1, 2), inputs_row['deadlines_us'])),
                first=Opportunity(0, (1, 2)),
                futures=(Opportunity(100000, (1,)), Opportunity(100000, (2,))),
                capacity=inputs_row['capacity'], record_budget=inputs_row['record_budget'],
                report_delay_us=inputs_row['report_delay_us'], start_us=0, end_us=200000,
                branch_hint=inputs_row['branch_hint'])
            results.append({'case': index, 'input': inputs_row, 'result': result})
        write(output / 'frontiers.json', results)
        lead = spec['lead_grid']
        deployment = []
        for advance, install, delay in product(lead['advance_us'], lead['install_delays_us'],
                                                lead['report_delays_us']):
            args = dict(packet_times=lead['packet_times_us'], notice_us=lead['change_us'] - advance,
                        install_delay_us=install, report_delay_us=delay,
                        initial_generated_us=lead['initial_generated_us'],
                        deadline_us=lead['deadline_us'], change_us=lead['change_us'],
                        end_us=lead['end_us'])
            deployment.append({'advance_us': advance, 'input': args,
                               'result': deployment_lead_certificate(**args)})
        write(output / 'deployment.json', deployment)
        counts = Counter((r['input']['branch_hint'], r['result']['offline_min_admitted'],
                          r['result']['causal_admitted']) for r in results if r['input']['group'] == 'grid')
        summary = {'frontier_cases': len(results), 'main_grid_cases': 144,
                   'heterogeneous_cases': 4, 'deployment_cases': len(deployment),
                   'frontier_counts': [{'branch_hint': h, 'offline': o, 'causal': c, 'cases': n}
                                       for (h, o, c), n in sorted(counts.items())],
                   'deployment_fulfilled': sum(r['result']['age']['fulfilled'] for r in deployment),
                   'deployment_with_unavoidable_wait_violation': sum(
                       r['result']['unavoidable_pre_report_violation_us'] > 0 for r in deployment),
                   'scope': '有限模型核对，无随机重复及网络性能结论'}
        write(output / 'summary.json', summary)
        manifest.update(status='passed', elapsed_s=time.monotonic() - started,
                        artifact_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in output.glob('*.json') if p.name != 'manifest.json'})
        write(output / 'manifest.json', manifest)
        return summary
    except Exception as error:
        manifest.update(status='failed', elapsed_s=time.monotonic() - started,
                        error_type=type(error).__name__, error=str(error))
        write(output / 'manifest.json', manifest)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(execute(parser.parse_args().output), ensure_ascii=False, indent=2))
