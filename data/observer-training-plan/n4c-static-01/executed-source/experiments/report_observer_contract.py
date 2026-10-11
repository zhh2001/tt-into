"""固定100ms观察、1s决策及已消费业务的工程合同。"""
import copy
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
METHOD_PATH = ROOT / 'data/network-comparison-plan/n3h-methods-01/method-contract.json'
METHOD_SHA256 = '9999635fcabf01f802e8c088a476e60960153ee4abf567b79b9e8c467c45e24e'
ORDER_SEED = 42426408


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def order(contract):
    result = []
    for profile in contract['profiles']:
        for method in ('static', 'phase_guard'):
            result.append(dict(profile, method=method, seed=11, parameter_index=contract['methods'][method]['parameter_index'],
                               adaptive=copy.deepcopy(contract['methods'][method]['adaptive']),
                               name=f"{profile['scenario']}-{profile['budget_level']}-s11-{method}"))
    random.Random(ORDER_SEED).shuffle(result)
    for index, entry in enumerate(result): entry['position'] = index
    return result


def validate(plan, current=False):
    if sha(METHOD_PATH) != METHOD_SHA256:
        raise ValueError('冻结方法合同摘要改变')
    contract = json.loads(METHOD_PATH.read_text())
    expected = {'status': 'prepared', 'stage': 'report_observer_correctness', 'business_seed': 11,
                'independent_test': False, 'observation_period_us': 100000, 'decision_period_us': 1000000,
                'duration_us': 10000000, 'warmup_us': [0, 2000000], 'measurement_window_us': [2000000, 10000000],
                'order_seed': ORDER_SEED, 'expected_cases': 8, 'expected_observation_slots': 100,
                'expected_decision_slots': 10, 'method_contract_sha256': METHOD_SHA256, 'execution_order': order(contract)}
    for key, value in expected.items():
        if plan.get(key) != value:
            raise ValueError('观察工程合同不符: ' + key)
    if current:
        for name, digest in plan['source_sha256'].items():
            if sha(ROOT / name) != digest:
                raise ValueError('首例前源码改变: ' + name)
    return contract
