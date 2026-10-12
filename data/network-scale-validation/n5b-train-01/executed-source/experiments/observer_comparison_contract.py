"""共同百毫秒观察的独立业务比较，业务生成前冻结全部配置。"""
import json
import random
from pathlib import Path

from network_comparison_contract import audit_consumption, sha
from run_matched_reference import METHODS, plain, settings
from observer_training_contract import LAYERS

ROOT = Path(__file__).resolve().parents[1]
METHOD_PATH = ROOT / 'data/observer-comparison-plan/n4c-methods-01/method-contract.json'
METHOD_SHA256 = 'a5fc4c8c08b460d2a7291e44c8b5a2869ea1f7bcecd6c2c901a26363212a2b9c'
ORDER_SEEDS = (42426414, 42426415)


def contract():
    if sha(METHOD_PATH) != METHOD_SHA256:
        raise ValueError('冻结八方法来源改变')
    return json.loads(METHOD_PATH.read_text())


def expected_order(methods, seeds):
    entries = []
    for block, order_seed in enumerate(ORDER_SEEDS, 1):
        members = []
        for profile in methods['profiles']:
            for seed in seeds:
                for method in METHODS:
                    preset = methods['methods'][method]
                    members.append(dict(profile, seed=seed, block=block, method=method,
                        parameter_index=preset['parameter_index'], adaptive=preset['adaptive'],
                        name=f"{profile['scenario']}-{profile['budget_level']}-s{seed}-b{block}-{method}"))
        random.Random(order_seed).shuffle(members)
        entries.extend(dict(row, order_seed=order_seed, position=len(entries)+i) for i,row in enumerate(members))
    return entries


def validate(plan, directory=None, current=False):
    methods = contract()
    fields = dict(status='prepared', execution_ready=True, expected_cases=256, business_count=4,
        independent_business_units=4, deployment_blocks=2, fresh_inputs_generated=False,
        independent_test=True, stage='controller_observer_independent_comparison',
        method_contract_sha256=METHOD_SHA256, method_contract=methods,
        order_seeds=list(ORDER_SEEDS), primary_observer='controller', secondary_observer='collector',
        observation_period_us=100000, decision_period_us=1000000,
        common_initialization=methods['profiles'], settings=plain(settings()), protocol=methods)
    for key,value in fields.items():
        if plan.get(key) != value: raise ValueError('独立比较合同不符: '+key)
    seeds = plan.get('fresh_business_seeds', [])
    audit = plan['seed_audit']
    if len(seeds)!=4 or len(set(seeds))!=4 or any(type(s) is not int or s<3001 for s in seeds):
        raise ValueError('四条独立业务分母错误')
    if seeds != audit['selected_fresh_seeds'] or set(seeds) & set(audit['excluded_seeds']):
        raise ValueError('新业务已消费或未按审计选择')
    selected=[]
    for seed in range(3001, max(seeds)+1):
        if seed not in set(audit['excluded_seeds']): selected.append(seed)
        if len(selected)==4: break
    if seeds != selected: raise ValueError('未取最小四个未消费整数')
    if plan['execution_order'] != expected_order(methods,seeds):
        raise ValueError('完整256例配置、顺序或部署块错误')
    if directory is not None:
        for name,value in (('seed-audit.json',audit),('frozen-static.json',methods['profiles']),
                           ('protocol.json',methods),('execution-order.json',plan['execution_order'])):
            if json.loads((directory/name).read_text()) != value: raise ValueError('准备副本改变: '+name)
        if sha(directory/'method-contract.json') != METHOD_SHA256: raise ValueError('方法副本改变')
        for name,digest in plan['source_sha256'].items():
            if sha(directory/'executed-source'/name) != digest: raise ValueError('执行源码快照改变')
    if current:
        from run_observer_comparison import SOURCES
        if set(plan['source_sha256']) != set(SOURCES): raise ValueError('执行源码集合不完整')
        for name,digest in plan['source_sha256'].items():
            if sha(ROOT/name) != digest: raise ValueError('执行源码改变: '+name)


def plan_fields(audit):
    methods=contract()
    return dict(status='prepared', execution_ready=True, expected_cases=256, business_count=4,
        independent_business_units=4, deployment_blocks=2, fresh_inputs_generated=False,
        independent_test=True, stage='controller_observer_independent_comparison',
        method_contract_sha256=METHOD_SHA256, method_contract=methods, seed_audit=audit,
        fresh_business_seeds=audit['selected_fresh_seeds'], order_seeds=list(ORDER_SEEDS),
        primary_observer='controller', secondary_observer='collector', observation_period_us=100000,
        decision_period_us=1000000, common_initialization=methods['profiles'], settings=plain(settings()),
        protocol=methods, execution_order=expected_order(methods,audit['selected_fresh_seeds']))
