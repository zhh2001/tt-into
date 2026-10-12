"""冻结既有候选的控制器目标训练及后续八方法协议，不生成业务。"""
import argparse
import json
from pathlib import Path
import shutil

from observer_training_contract import (ROOT, CANDIDATE_PATH, CANDIDATE_SHA256, METHOD_PATH, METHOD_SHA256,
    ORDER_SEED, expected_order, protocol, sha, validate)
from network_comparison_contract import audit_consumption
from run_observer_training import SOURCES
from analyze_report_observer import write


def prepare(output):
    audit = audit_consumption(ROOT / 'data')
    if not {3001, 3002, 3003, 3004}.issubset(audit['consumed_seeds']): raise ValueError('历史新业务消费记录不完整')
    audit['scope'] += '；所列未消费候选仅为审计观察，不是N4c正式测试清单，静态及全局参数冻结后须重审计'
    candidates = json.loads(CANDIDATE_PATH.read_text())
    plan = {'status': 'prepared', 'stage': 'controller_observer_static_training', 'independent_test': False,
        'training_seeds': [11, 29], 'expected_cases': 192, 'repeats_per_candidate_business': 1,
        'candidate_plan_sha256': CANDIDATE_SHA256, 'method_contract_sha256': METHOD_SHA256,
        'primary_observer': 'controller', 'observation_period_us': 100000, 'decision_period_us': 1000000,
        'duration_us': 10000000, 'order_seed': ORDER_SEED, 'protocol': protocol(), 'seed_audit': audit,
        'execution_order': expected_order(candidates), 'source_sha256': {n: sha(ROOT / n) for n in SOURCES},
        'scope': '24/6561有限候选双训练业务，控制器损失选择；不生成校准或未见测试，选中后待重复确认'}
    validate(plan, current=True)
    output.mkdir(parents=True, exist_ok=False)
    for source, name in ((CANDIDATE_PATH, 'candidate-plan.json'), (METHOD_PATH, 'method-contract.json')):
        shutil.copyfile(source, output / name)
    for name, value in (('training-plan.json', plan), ('protocol.json', plan['protocol']), ('seed-audit.json', audit)):
        write(output / name, value)
    for name in SOURCES:
        target = output / 'executed-source' / name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    write(output / 'manifest.json', {'status': 'prepared', 'business_generated': False, 'expected_cases': 192,
        'artifact_sha256': {str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}})
    print(json.dumps({'status': 'prepared', 'cases': 192, 'new_test_generated': False,
                     'audited_manifests': len(audit['historical_manifests']), 'unconsumed_candidates': audit['selected_fresh_seeds']}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', type=Path, required=True)
    prepare(p.parse_args().output)
