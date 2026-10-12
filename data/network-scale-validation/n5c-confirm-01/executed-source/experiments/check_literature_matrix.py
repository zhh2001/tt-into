"""按方法分派独立核对，验收完整训练、校准或十方法矩阵。"""
import argparse
import json
from pathlib import Path

from check_literature_observer import check_case as literature_case
from check_report_observer import check_case as reference_case
from check_matched_network import load, rows, sha
from check_rolling_reference import same
from literature_stage_contract import validate


def check_case(directory, rpc):
    method = load(directory / 'result.json')['method']
    return literature_case(directory, rpc) if method in ('intopp_due', 'd_into_tt') else reference_case(directory, rpc)


def audit(run):
    m, plan = load(run / 'manifest.json'), load(run / 'stage-plan.json')
    validate(plan)
    same(m['status'], 'passed', '实际完整矩阵')
    same(sha(run / 'stage-plan.json'), m['plan_sha256'], '首例前计划绑定')
    same(m['expected_cases'], plan['expected_cases'], '完整运行分母')
    same(m['source_sha256'], plan['source_sha256'], '实际执行版本')
    same(m['execution_order'], plan['execution_order'], '实际执行顺序')
    same(load(run / 'execution-order.json'), plan['execution_order'], '顺序归档')
    same([c['name'] for c in m['cases']], [e['name'] for e in plan['execution_order']], '完整案例身份')
    for name, digest in m['source_sha256'].items(): same(sha(run / 'executed-source' / name), digest, '实际源码快照')
    for name, digest in m['artifact_sha256'].items(): same(sha(run / name), digest, '实际产物摘要')
    same(len(m['blocks']), plan['deployment_blocks'], '独立部署块数')
    previous = 0
    for block in m['blocks']:
        same(block['status'], 'passed', '部署与清理成功')
        if not previous <= block['started_monotonic_ns'] <= block['ready_monotonic_ns'] <= block['cleaned_monotonic_ns']:
            raise ValueError('部署时序错误')
        members = [c for c in m['cases'] if c['block'] == block['block']]
        same(len(members), plan['expected_cases'] // plan['deployment_blocks'], '每部署块完整分母')
        if not all(block['ready_monotonic_ns'] <= c['origin_monotonic_ns'] <= c['ended_monotonic_ns'] <= block['cleaned_monotonic_ns'] for c in members):
            raise ValueError('案例越过所属部署')
        if any(x is None for x in block['switch_returncodes']): raise ValueError('交换机未结束')
        previous = block['cleaned_monotonic_ns']
    rpc, results = rows(run / 'rpc-operations.jsonl'), []
    for case, entry in zip(m['cases'], plan['execution_order']):
        for key in entry: same(case[key], entry[key], '实际配置、业务和预设')
        opts = load(run / case['name'] / 'algorithm-settings.json')
        same(opts['adaptive'], entry['adaptive'], '实际旧策略参数')
        same(opts['literature'], entry['literature'], '实际文献参数')
        result = check_case(run / case['name'], rpc)
        same(result['status'], 'passed', '线格式、状态与合法来源')
        results.append(result)
    keys = ('packet_count', 'report_count', 'decision_count', 'successful_updates', 'checks', 'live_control_checks',
            'observer_checks', 'cache_and_budget_checks', 'observation_slots', 'completed_observations',
            'skipped_observations', 'visible_reports', 'shared_cache_advances')
    return dict(status='passed', cases=results, **{k: sum(r[k] for r in results) for k in keys})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--run', type=Path, required=True)
    result = audit(p.parse_args().run)
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, ensure_ascii=False))
