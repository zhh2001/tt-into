"""冻结有限预算及共同报告周期敏感性的业务、顺序与实际入口。"""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import random

from scalable_contract import ROOT, sha, source_files
from scale_comparison_execution import read_scope as comparison_scope
from scale_preflight_contract import validate_paths
from scale_training_contract import budget_for
from network_comparison_contract import audit_consumption
from orchestration.network_spec import FixedNetwork

SCOPE = 'data/network-scale-sensitivity-plan/n5e-scope-01/sensitivity-scope.json'
ORDER_SEED = 52705
METHODS = ('static', 'phase_guard')
CONDITIONS = (('B085', .85, 100000), ('B100', 1., 100000),
              ('B115', 1.15, 100000), ('O250', 1., 250000), ('O1000', 1., 1000000))


def read_scope():
    path = ROOT / SCOPE
    manifest = json.loads((path.parent / 'manifest.json').read_text())
    for name, digest in manifest['artifact_sha256'].items():
        if sha(path.parent / name) != digest:
            raise ValueError('敏感性预声明产物改变 ' + name)
    scope = json.loads(path.read_text())
    prior = comparison_scope()
    expected = [dict(condition_id=c, data_budget_multiplier=m, observation_period_us=p)
                for c, m, p in CONDITIONS]
    if (scope['status'], scope['execution_ready'], scope['total_cases']) != ('scope_frozen', False, 80):
        raise ValueError('敏感性范围身份改变')
    if scope['methods'] != list(METHODS) or scope['conditions'] != expected:
        raise ValueError('方法或五条件改变')
    if scope['method_parameters'] != prior['method_parameters']:
        raise ValueError('原全局参数改变')
    source = ROOT / 'data/network-scale-plan/n5d-scope-01/comparison-scope.json'
    if scope['source_scope_sha256'] != sha(source):
        raise ValueError('共同初始化来源改变')
    cell = next(c for c in prior['cells'] if (c['network_id'], c['scenario']) == ('n5f4', 'shared_priority'))
    if scope['cell'] != cell:
        raise ValueError('冻结C29改变')
    if (scope['independent_business_count'], scope['deployment_blocks'], scope['decision_period_us'],
        scope['api_bytes_per_s'], scope['local_notice_payload_bytes_per_s']) != (4, 2, 1000000, 1200, 128):
        raise ValueError('敏感性分母或控制额度改变')
    return scope, prior['networks']['n5f4']


def condition_budget(spec, multiplier):
    budget = copy.deepcopy(budget_for(spec))
    budget['link_bytes_per_s'] = {k: v * multiplier for k, v in budget['link_bytes_per_s'].items()}
    budget['export_bytes_per_s'] *= multiplier
    return budget


def order(scope, topology, seeds, mode):
    if mode == 'comparison':
        if len(seeds) != 4 or len(set(seeds)) != 4 or any(type(s) is not int or s < 3001 for s in seeds):
            raise ValueError('正式敏感性需要四条不同新业务')
        conditions, blocks = scope['conditions'], (0, 1)
    elif mode == 'smoke':
        if seeds != [11]:
            raise ValueError('烟测只复用已消费训练11')
        conditions = [c for c in scope['conditions'] if c['condition_id'] in ('B085', 'O1000')]
        blocks = (0,)
    else:
        raise ValueError('未知敏感性模式')
    spec = FixedNetwork(topology)
    entries, batches = [], []
    for block in blocks:
        group = [dict(name=f'n5f4-{c["condition_id"]}-s{seed}-{method}',
            **copy.deepcopy(scope['cell']), **copy.deepcopy(c), business_seed=seed, block=block,
            method=method, **copy.deepcopy(scope['method_parameters'][method]),
            budget=condition_budget(spec, c['data_budget_multiplier']), decision_period_us=1000000,
            scope='冻结运行点的五条件有限敏感性' if mode == 'comparison' else '训练11的低预算与秒级观察端点烟测')
            for c in conditions for seed in seeds for method in METHODS]
        random.Random(ORDER_SEED + block).shuffle(group)
        for entry in group:
            entry['position'] = len(entries)
            entries.append(entry)
        batches.append(dict(batch_id=f'b{block:02d}', block=block, network_id='n5f4', entries=group))
    return entries, batches


def definition(mode, audit):
    scope, topology = read_scope()
    seeds = list(audit['selected_fresh_seeds']) if mode == 'comparison' else [11]
    if mode == 'comparison' and set(seeds) & set(audit['excluded_seeds']):
        raise ValueError('新业务与持久消费证据重合')
    entries, batches = order(scope, topology, seeds, mode)
    return dict(stage='n5e_finite_sensitivity_execution', status='prepared', execution_ready=True, mode=mode,
        sensitivity_scope=dict(path=SCOPE, sha256=sha(ROOT / SCOPE)), expected_cases=len(entries),
        execution_order=entries, batches=batches, networks={'n5f4': topology}, business_seeds=seeds,
        independent_test=mode == 'comparison', blocks=[0, 1] if mode == 'comparison' else [0],
        order_seed=ORDER_SEED, seed_audit=audit, observation_periods_us=sorted({e['observation_period_us'] for e in entries}),
        expected_observation_slots=sum(10000000 // e['observation_period_us'] for e in entries),
        decision_period_us=1000000, capacity=1, duration_us=10000000,
        measurement_window_us=[2000000, 10000000],
        batch_contract=dict(packet_id_base_per_case=1000, max_cases=40,
            validation_identity_domain='逐批IPv4 ID用于抓包核对，在线身份为FlowTag/Session'),
        source_sha256={n: sha(ROOT / n) for n in source_files()})


def validate(plan, current=False, output=None):
    expected = definition(plan['mode'], plan['seed_audit'])
    for key in expected.keys() - {'source_sha256'}:
        if plan.get(key) != expected[key]:
            raise ValueError('敏感性冻结字段改变 ' + key)
    if current and plan['source_sha256'] != expected['source_sha256']:
        raise ValueError('冻结后源码改变')
    identities = Counter((e['condition_id'], e['business_seed'], e['block'], e['method']) for e in plan['execution_order'])
    if len(identities) != plan['expected_cases'] or set(identities.values()) != {1}:
        raise ValueError('敏感性完整配对身份缺失或重复')
    for batch in plan['batches']:
        if len(batch['entries']) > 40 or (len(batch['entries']) - 1) * 1000 + 320 > 65536:
            raise ValueError('验证编号超出范围')
        if output is not None:
            validate_paths(dict(networks=plan['networks'], execution_order=batch['entries']), output / batch['batch_id'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('comparison', 'smoke'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    audit = audit_consumption(ROOT / 'data')
    plan = definition(args.mode, audit)
    validate(plan, True)
    args.output.mkdir(parents=True, exist_ok=False)
    for name, value in (('stage-plan.json', plan), ('seed-audit.json', audit)):
        (args.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    (args.output / 'sensitivity-scope.json').write_bytes((ROOT / SCOPE).read_bytes())
    record = dict(status='prepared', execution_ready=True, business_generated=False, network_started=False,
        artifact_sha256={p.name: sha(p) for p in args.output.iterdir() if p.is_file()})
    (args.output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(status='prepared', cases=plan['expected_cases'], batches=len(plan['batches']),
                         business_seeds=plan['business_seeds'], observation_slots=plan['expected_observation_slots'])))
