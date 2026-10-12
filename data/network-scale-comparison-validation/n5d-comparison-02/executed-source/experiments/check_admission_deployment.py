"""独立核对公开执行来源、控制寄存器目标与汇总口径。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs', nargs=4, type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    values, inputs = [], {}
    for run in a.runs:
        path = run / 'summary.json'
        s = json.loads(path.read_text())
        if s['status'] != 'passed':
            raise ValueError('执行状态不通过')
        for name, sha in s['source_sha256'].items():
            if digest(run / 'executed-source' / name) != sha:
                raise ValueError('执行源码摘要错误')
        for name, sha in s['artifact_sha256'].items():
            if digest(run / name) != sha:
                raise ValueError('产物摘要错误')
        operations = [json.loads(r) for r in (run / 'rpc-operations.jsonl').read_text().splitlines()]
        for c in s['cases']:
            calls, serialized = 0, 0
            for update in c['updates']:
                begin, end = update['rpc_indices']
                if end - begin != len(update['writes']):
                    raise ValueError('更新调用数错误')
                for r, w in zip(operations[begin:end], update['writes']):
                    if r['method'] != 'bm_register_write' or r['node'] != w['node'] or r['arguments'] != [w['register'], w['flow'], w['value']] or r['kind'] != update['kind']:
                        raise ValueError('实际调用目标、类型或参数与计划不符')
                    if r['before_monotonic_ns'] < update['drained_monotonic_ns']:
                        raise ValueError('在途数据未排空就更新')
                    calls += 1
                    serialized += r['request_serialized_bytes'] + r['reply_serialized_bytes']
            if any(r['method'] == 'bm_register_reset' for update in c['updates']
                   for r in operations[slice(*update['rpc_indices'])]):
                raise ValueError('运行中重置了成功状态')
            if serialized != sum(r['request_serialized_bytes'] + r['reply_serialized_bytes']
                                 for r in c['update_execution_checks']):
                raise ValueError('控制费用汇总不符')
            values.append({'run_id': s['run_id'], 'point': c['candidate']['point_index'],
                           'capacity': c['capacity'], 'diagnostics': s['diagnostics'],
                           'threshold_margin_us': c['candidate'].get('threshold_margin_us', 0),
                           'business_packets': c['packet_count'], 'report_packets': c['report_count'],
                           'inband_per_link_bytes': c['measured_cost']['inband_per_link_bytes'],
                           'export_bytes': c['measured_cost']['export_bytes_with_minimum_ethernet_padding'],
                           'data_budget_feasible': c['original_point_data_budget_feasible'],
                           'age_fulfilled': c['all_tasks_fulfilled_by_upper_age'],
                           'runtime_rpc_calls': calls, 'runtime_serialized_request_reply_bytes': serialized})
        inputs[str(path)] = digest(path)
    a.output.mkdir(parents=True, exist_ok=False)
    archive = a.output / 'executed-source/experiments'
    archive.mkdir(parents=True)
    shutil.copyfile(__file__, archive / Path(__file__).name)
    result = {'status': 'passed', 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'scope': '独立来源、更新参数、保留成功状态及费用核对，不是新增性能试验',
              'input_sha256': inputs, 'audit_source_sha256': digest(Path(__file__)), 'cases': values,
              'execution_cases': len(values), 'business_packets': sum(r['business_packets'] for r in values),
              'within_data_budget_cases': sum(r['data_budget_feasible'] for r in values),
              'all_task_age_fulfilled_cases': sum(r['age_fulfilled'] for r in values)}
    (a.output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('status', 'execution_cases', 'business_packets',
                                            'within_data_budget_cases', 'all_task_age_fulfilled_cases')}))


if __name__ == '__main__':
    main()
