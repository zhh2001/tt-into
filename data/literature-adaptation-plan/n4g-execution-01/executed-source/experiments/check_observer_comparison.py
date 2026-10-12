"""独立验收完整256例两块、共同报告批次及实际静态费用。"""
import argparse
import json
from pathlib import Path

from check_report_observer import check_case
from check_matched_network import load, rows, sha
from check_rolling_reference import same
from observer_comparison_contract import validate


def audit(run):
    manifest, plan = load(run / 'manifest.json'), load(run / 'comparison-plan.json')
    validate(plan)
    same(manifest['status'], 'passed', '完整独立比较实际执行')
    same(manifest['expected_cases'], 256, '完整比较分母')
    same(sha(run / 'comparison-plan.json'), manifest['plan_sha256'], '执行前训练摘要')
    same(load(run / 'protocol.json'), plan['protocol'], '控制器主目标与后续八方法协议')
    same(load(run / 'seed-audit.json'), plan['seed_audit'], '实际持久消费审计来源')
    same(manifest['execution_order'], plan['execution_order'], '冻结训练执行顺序')
    same(load(run / 'execution-order.json'), plan['execution_order'], '独立顺序留档')
    same([c['name'] for c in manifest['cases']], [e['name'] for e in plan['execution_order']], '完整双业务与候选分母')
    same(manifest['source_sha256'], plan['source_sha256'], '首例前与实际源码一致')
    for name, digest in manifest['source_sha256'].items():
        same(sha(run / 'executed-source' / name), digest, '实际执行源码快照')
    for name, digest in manifest['artifact_sha256'].items():
        same(sha(run / name), digest, '完整产物摘要')
    same([b['block'] for b in manifest['blocks']], [1, 2], '两个独立网络部署块')
    previous = 0
    for block in manifest['blocks']:
        same(block['status'], 'passed', '块启动和清理成功')
        if not previous <= block['started_monotonic_ns'] <= block['ready_monotonic_ns'] <= block['cleaned_monotonic_ns']:
            raise ValueError('部署块启动/清理时序错误')
        members = [c for c in manifest['cases'] if c['block'] == block['block']]
        same(len(members), 128, '每块完整128例')
        if not all(block['ready_monotonic_ns'] <= c['origin_monotonic_ns'] <= c['ended_monotonic_ns'] <= block['cleaned_monotonic_ns'] for c in members):
            raise ValueError('案例越过所属网络部署范围')
        same(len(block['switch_pids']), 4, '四个实际交换机进程')
        if any(v is None for v in block['switch_returncodes']): raise ValueError('交换机清理未完成')
        same(block['capture_directories'], [f"node-{n}-b{block['block']}" for n in range(1, 5)], '分块捕获不覆盖')
        previous = block['cleaned_monotonic_ns']
    same(load(run / 'frozen-static.json'), plan['common_initialization'], '全部方法共同初始化')
    same(sha(run / 'method-contract.json'), plan['method_contract_sha256'], '实际冻结方法来源')
    rpc = rows(run / 'rpc-operations.jsonl'); results = []
    for case, entry in zip(manifest['cases'], plan['execution_order']):
        for key in entry: same(case[key], entry[key], '实际候选、业务与顺序')
        opts = load(run / case['name'] / 'algorithm-settings.json')
        same(opts['adaptive'], entry['adaptive'], '完整共同策略参数')
        result = check_case(run / case['name'], rpc)
        same(result['status'], 'passed', '真实格式、配置与合法来源')
        if entry['method'] == 'static': same(result['successful_updates'], 0, '静态无在线更新')
        results.append(result)
    keys = ('packet_count', 'report_count', 'decision_count', 'successful_updates', 'checks', 'live_control_checks',
            'observer_checks', 'cache_and_budget_checks', 'observation_slots', 'completed_observations',
            'skipped_observations', 'visible_reports', 'shared_cache_advances')
    return dict(status='passed', cases=results, **{k: sum(r[k] for r in results) for k in keys})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--run', type=Path, required=True)
    result = audit(p.parse_args().run)
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, ensure_ascii=False))
