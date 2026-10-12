"""独立验收完整192例、共同报告批次及实际静态费用。"""
import argparse
import json
from pathlib import Path

from check_report_observer import check_case
from check_matched_network import load, rows, sha
from check_rolling_reference import same
from observer_training_contract import validate


def audit(run):
    manifest, plan = load(run / 'manifest.json'), load(run / 'training-plan.json')
    validate(plan)
    same(manifest['status'], 'passed', '完整静态训练实际执行')
    same(manifest['expected_cases'], 192, '完整训练分母')
    same(sha(run / 'training-plan.json'), manifest['plan_sha256'], '执行前训练摘要')
    same(load(run / 'candidate-plan.json'), load(Path(__file__).resolve().parents[1] / 'data/network-static-plan/n3e-candidates-01/candidate-plan.json'), '有限候选来源')
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
    rpc = rows(run / 'rpc-operations.jsonl'); results = []
    for case, entry in zip(manifest['cases'], plan['execution_order']):
        for key in entry: same(case[key], entry[key], '实际候选、业务与顺序')
        opts = load(run / case['name'] / 'algorithm-settings.json')
        same(opts['adaptive'], entry['adaptive'], '完整共同策略参数')
        result = check_case(run / case['name'], rpc)
        same(result['status'], 'passed', '真实格式、配置与合法来源')
        same(result['successful_updates'], 0, '静态无在线更新')
        results.append(result)
    keys = ('packet_count', 'report_count', 'decision_count', 'successful_updates', 'checks', 'live_control_checks',
            'observer_checks', 'cache_and_budget_checks', 'observation_slots', 'completed_observations',
            'skipped_observations', 'visible_reports', 'shared_cache_advances')
    return dict(status='passed', cases=results, **{k: sum(r[k] for r in results) for k in keys})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--run', type=Path, required=True)
    result = audit(p.parse_args().run)
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, ensure_ascii=False))
