"""文献适配八例工程合同，方法定位与参数冻结于业务生成前。"""
import copy
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'data/observer-comparison-plan/n4c-methods-01/method-contract.json'
REFERENCE_SHA256 = 'a5fc4c8c08b460d2a7291e44c8b5a2869ea1f7bcecd6c2c901a26363212a2b9c'
ORDER_SEED = 42426416
PARAMETERS = dict(value_scale=144.0, average_change_bytes=12.0, early_fraction=0.0, weighted_dependencies=False)
METHODS = ('intopp_due', 'd_into_tt')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def method_contract():
    if sha(REFERENCE) != REFERENCE_SHA256:
        raise ValueError('共同初始化来源改变')
    reference = json.loads(REFERENCE.read_text())
    return dict(status='correctness_parameters_frozen', profiles=reference['profiles'],
        adaptive=reference['methods']['phase_joint']['adaptive'], literature=PARAMETERS,
        methods=list(METHODS), reference_contract_sha256=REFERENCE_SHA256,
        sources={'intopp_due': dict(doi='10.1109/LCOMM.2019.2946562', equations=[1, 3, 5, 6, 7],
            mapping='单条目期限依赖，接收新近度，共同TT逐包容量；不含聚类'),
            'd_into_tt': dict(doi='10.1109/TNSM.2025.3530432', equations=[3, 4, 6, 10, 25, 26],
            mapping='管理权重的频率达成收益、已确认许可虚拟队列、共同TT逐包容量')},
        observation_period_us=100000, decision_period_us=1000000, independent_test=False)


def order(contract):
    entries = [dict(profile, method=method, seed=11, block=1, candidate_index=None, parameter_index=None,
        adaptive=copy.deepcopy(contract['adaptive']), literature=copy.deepcopy(PARAMETERS),
        name=f"{profile['scenario']}-{profile['budget_level']}-s11-{method}")
        for profile in contract['profiles'] for method in METHODS]
    random.Random(ORDER_SEED).shuffle(entries)
    return [dict(entry, position=i) for i, entry in enumerate(entries)]


def validate(plan, current=False):
    contract = method_contract()
    expected = dict(status='prepared', stage='literature_tt_correctness', business_seed=11,
        independent_test=False, observation_period_us=100000, decision_period_us=1000000,
        duration_us=10000000, warmup_us=[0, 2000000], measurement_window_us=[2000000, 10000000],
        order_seed=ORDER_SEED, expected_cases=8, expected_observation_slots=100,
        expected_decision_slots=10, execution_order=order(contract), method_contract=contract)
    for key, value in expected.items():
        if plan.get(key) != value:
            raise ValueError('文献工程合同不符: ' + key)
    if current:
        for name, digest in plan['source_sha256'].items():
            if sha(ROOT / name) != digest:
                raise ValueError('首例前源码改变: ' + name)
    return contract
