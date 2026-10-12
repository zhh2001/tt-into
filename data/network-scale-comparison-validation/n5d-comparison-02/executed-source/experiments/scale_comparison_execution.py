"""绑定九个共同初始化与原全局参数，冻结独立业务比较顺序。"""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import random

from scalable_contract import ROOT, sha, source_files
from scale_preflight_contract import validate_paths, method_parameters
from scale_training_contract import budget_for
from network_comparison_contract import audit_consumption
from orchestration.network_spec import FixedNetwork

SCOPE = 'data/network-scale-plan/n5d-scope-01/comparison-scope.json'
ORDER_SEED = 52704
METHODS = ('static', 'feedback', 'phase_joint', 'phase_guard', 'intopp_due', 'd_into_tt')


def read_scope():
    path = ROOT / SCOPE
    for name, digest in json.loads((path.parent / 'manifest.json').read_text())['artifact_sha256'].items():
        if sha(path.parent / name) != digest:
            raise ValueError('规模方法范围产物改变 ' + name)
    scope = json.loads(path.read_text())
    if (scope['status'], scope['execution_ready'], scope['expected_cases']) != ('methods_and_scope_frozen', False, 432):
        raise ValueError('规模比较范围身份改变')
    if scope['method_parameters'] != method_parameters() or scope['methods'] != list(METHODS):
        raise ValueError('原生或文献全局参数改变')
    origin = scope['source_analysis']
    if sha(ROOT / origin['path'] / 'manifest.json') != origin['manifest_sha256'] or \
            sha(ROOT / origin['path'] / 'frozen-static.json') != origin['frozen_static_sha256']:
        raise ValueError('共同初始化来源改变')
    return scope


def order(scope, seeds, mode):
    if mode == 'comparison':
        if len(seeds) != 4 or len(set(seeds)) != 4 or any(type(s) is not int or s < 3001 for s in seeds):
            raise ValueError('规模比较需要四条不同的新业务')
        blocks = (0, 1)
        cells = scope['cells']
        methods = METHODS
    elif mode == 'smoke':
        if seeds != [11]:
            raise ValueError('烟测只复用已消费训练11')
        blocks = (0,)
        cells = [c for c in scope['cells'] if (c['network_id'], c['scenario']) == ('n5f4', 'shared_priority')]
        methods = ('static', 'phase_joint', 'd_into_tt')
    else:
        raise ValueError('未知规模比较模式')
    entries, batches = [], []
    for block in blocks:
        networks = list(dict.fromkeys(c['network_id'] for c in cells))
        random.Random(ORDER_SEED + block).shuffle(networks)
        for network in networks:
            spec = FixedNetwork(scope['networks'][network])
            group = [dict(name=f'{network}-{c["scenario"]}-s{seed}-{method}', network_id=network,
                scenario=c['scenario'], candidate_id=c['candidate_id'], business_seed=seed, block=block,
                method=method, initial_config=copy.deepcopy(c['initial_config']),
                **copy.deepcopy(scope['method_parameters'][method]), budget=budget_for(spec),
                scope='四条独立业务的冻结六方法规模比较' if mode == 'comparison' else '已消费训练11的规模连接烟测')
                for c in cells if c['network_id'] == network for seed in seeds for method in methods]
            random.Random(ORDER_SEED + 1000 * block + 100 * spec.record['sampling_nodes'] + len(spec.record['flows'])).shuffle(group)
            for entry in group:
                entry['position'] = len(entries)
                entries.append(entry)
            batches.append(dict(batch_id=f'b{len(batches):02d}', block=block, network_id=network, entries=group))
    return entries, batches


def definition(mode, audit):
    scope = read_scope()
    seeds = list(audit['selected_fresh_seeds']) if mode == 'comparison' else [11]
    if mode == 'comparison' and set(seeds) & set(audit['excluded_seeds']):
        raise ValueError('新业务与既有消费证据重合')
    entries, batches = order(scope, seeds, mode)
    return dict(stage='n5d_scale_comparison_execution', status='prepared', execution_ready=True, mode=mode,
        comparison_scope=dict(path=SCOPE, sha256=sha(ROOT / SCOPE)), expected_cases=len(entries),
        execution_order=entries, batches=batches, networks=scope['networks'], business_seeds=seeds,
        independent_test=mode == 'comparison', blocks=[0, 1] if mode == 'comparison' else [0],
        order_seed=ORDER_SEED, seed_audit=audit, observation_period_us=100000, decision_period_us=1000000,
        capacity=1, duration_us=10000000, measurement_window_us=[2000000, 10000000],
        batch_contract=dict(packet_id_base_per_case=1000, max_cases=48,
            validation_identity_domain='逐批IPv4 ID只用于抓包核对，在线身份为FlowTag/Session'),
        source_sha256={n:sha(ROOT / n) for n in source_files()})


def validate(plan, current=False, output=None):
    expected = definition(plan['mode'], plan['seed_audit'])
    for key in expected.keys() - {'source_sha256'}:
        if plan.get(key) != expected[key]:
            raise ValueError('规模比较冻结字段改变 ' + key)
    if current and plan['source_sha256'] != expected['source_sha256']:
        raise ValueError('规模比较冻结后源码改变')
    identities = Counter((e['network_id'],e['scenario'],e['business_seed'],e['block'],e['method']) for e in plan['execution_order'])
    if len(identities) != plan['expected_cases'] or set(identities.values()) != {1}:
        raise ValueError('完整配对身份重复或缺失')
    for batch in plan['batches']:
        spec = FixedNetwork(plan['networks'][batch['network_id']])
        if len(batch['entries']) > 48 or (len(batch['entries'])-1)*1000+80*len(spec.record['flows']) > 65536:
            raise ValueError('逐批验证编号超出IPv4 ID空间')
        if output is not None:
            validate_paths(dict(networks={batch['network_id']:spec.record},execution_order=batch['entries']),output / batch['batch_id'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('comparison','smoke'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    audit = audit_consumption(ROOT / 'data')
    plan = definition(args.mode, audit)
    validate(plan, True)
    args.output.mkdir(parents=True, exist_ok=False)
    for name, value in (('stage-plan.json',plan),('seed-audit.json',audit)):
        (args.output / name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    (args.output / 'comparison-scope.json').write_bytes((ROOT / SCOPE).read_bytes())
    manifest = dict(status='prepared',execution_ready=True,business_generated=False,network_started=False,
        artifact_sha256={p.name:sha(p) for p in args.output.iterdir() if p.is_file()})
    (args.output / 'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(status='prepared',cases=plan['expected_cases'],batches=len(plan['batches']),business_seeds=plan['business_seeds'])))
