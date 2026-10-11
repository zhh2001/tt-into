"""共同百毫秒入口的184例动态全局校准合同。"""
import json
import math
import random
from observer_training_contract import ROOT, METHOD_PATH, METHOD_SHA256, protocol, rates, LAYERS, sha
from run_matched_reference import AdaptiveSettings, METHODS, GUARDED, PARAMETERS, plain, settings
from dynamic_calibration_contract import normalized_excess

CONFIRMATION = ROOT / 'data/observer-confirmation-validation/n4c-confirmation-01'
ORDER_SEED = 42426413
INPUTS = {
    'data/observer-confirmation-validation/n4c-confirmation-01/manifest.json': 'fcb662b8eb7ae510357b62bdd1637b7255d603bca8668d76e076ba35e32fd91a',
    'data/observer-confirmation-validation/n4c-confirmation-01/frozen-static.json': 'e94f2992c4dd5d0c72962cfc645f69fb83b45b7ffb09413c7566db16966db1df',
    'data/observer-confirmation-audit/n4c-confirmation-01/independent-checks.json': 'a5713b508268381ae34c4da5e7e76cb786b6b484447445193ddfc0617504abef',
}


def initialization():
    for name, digest in INPUTS.items():
        if sha(ROOT / name) != digest: raise ValueError('共同初始化来源改变: ' + name)
    return json.loads((CONFIRMATION / 'frozen-static.json').read_text())


def presets():
    return [dict(method=m, parameter_index=i, adaptive=plain(PARAMETERS[i] if i is not None else AdaptiveSettings()))
            for m in METHODS for i in (range(6) if m in GUARDED else (None,))]


def expected_order():
    entries = []
    for cell in initialization():
        for seed in (1301, 1423):
            for preset in presets():
                index = preset['parameter_index']
                suffix = 'fixed' if index is None else f'p{index:02d}'
                entries.append(dict(preset, scenario=cell['scenario'], budget_level=cell['budget_level'], seed=seed,
                    initial_config=cell['selected_config'],
                    name=f"{cell['scenario']}-{cell['budget_level']}-s{seed}-{preset['method']}-{suffix}"))
    random.Random(ORDER_SEED).shuffle(entries)
    return [dict(row, position=i, order_seed=ORDER_SEED) for i, row in enumerate(entries)]


def calibration_protocol():
    value = protocol()
    value.update(static_selection_pending=False, common_initialization_frozen=True,
                 calibration_expected_cases=184, calibration_repeats_per_setting_business=1,
                 calibration_order_seed=ORDER_SEED)
    return value


def plan_fields():
    return dict(status='prepared', stage='controller_observer_dynamic_calibration', independent_test=False,
        training_seeds=[11,29], calibration_seeds=[1301,1423], fresh_test_seeds_generated=False,
        expected_cases=184, cases_per_guarded_parameter=8, repeats_per_setting_business=1,
        order_seed=ORDER_SEED, methods=list(METHODS), presets=presets(), common_initialization=initialization(),
        settings=plain(settings()), input_sha256=INPUTS, method_contract_sha256=METHOD_SHA256,
        primary_observer='controller', observation_period_us=100000, decision_period_us=1000000,
        duration_us=10000000, protocol=calibration_protocol(), execution_order=expected_order())


def validate(plan, directory=None, current=False):
    if sha(METHOD_PATH) != METHOD_SHA256: raise ValueError('方法与预算来源改变')
    for key,value in plan_fields().items():
        if plan.get(key) != value: raise ValueError('动态校准合同不符: '+key)
    if directory is not None:
        for name,value in (('frozen-static.json',plan['common_initialization']),('protocol.json',plan['protocol'])):
            if json.loads((directory/name).read_text()) != value: raise ValueError('准备副本改变: '+name)
        if sha(directory/'method-contract.json') != METHOD_SHA256: raise ValueError('方法副本改变')
        for name,digest in plan['source_sha256'].items():
            if sha(directory/'executed-source'/name) != digest: raise ValueError('执行源码快照改变')
    if current:
        from run_observer_calibration import SOURCES
        if set(plan['source_sha256']) != set(SOURCES): raise ValueError('执行源码集合不完整')
        for name,digest in plan['source_sha256'].items():
            if sha(ROOT/name) != digest: raise ValueError('执行源码改变: '+name)


def select(plan, summaries):
    validate(plan)
    keys=('scenario','budget_level','seed','method','parameter_index')
    actual=[tuple(r[k] for k in keys) for r in summaries]
    expected={tuple(e[k] for k in keys) for e in plan['execution_order']}
    if len(actual)!=184 or len(set(actual))!=184 or set(actual)!=expected:
        raise ValueError('完整184例分母不符')
    scores, frozen=[],[]
    for method in GUARDED:
        eligible=[]
        for index in range(6):
            values=[r for r in summaries if (r['method'],r['parameter_index'])==(method,index)]
            excess=[normalized_excess(rates(r),r['scenario'],r['budget_level']) for r in values]
            valid=all(type(r[k]) in (int,float) and math.isfinite(r[k]) for r in values
                      for k in ('controller_loss_lower','controller_loss_upper'))
            valid=valid and all(0<=r['controller_loss_lower']<=r['controller_loss_upper']<=4+1e-10 for r in values)
            valid=valid and all(x is not None for x in excess)
            rank=[sum(r['window_feasibility']!='within' for r in values),sum(excess),
                  sum(r['controller_loss_upper'] for r in values)/8,sum(r['successful_updates'] for r in values),index] if valid else None
            score=dict(method=method,parameter_index=index,adaptive=plain(PARAMETERS[index]),case_count=8,
                evaluable=valid,feasible=valid and rank[0]==0,rank=rank,
                case_names=[r['case'] for r in values],window_statuses={r['case']:r['window_feasibility'] for r in values})
            scores.append(score)
            if valid: eligible.append(score)
        winner=min(eligible,key=lambda r:r['rank']) if eligible else None
        frozen.append(dict(method=method, selected_parameter=winner['parameter_index'] if winner else None,
            adaptive=winner['adaptive'] if winner else None, selected_rank=winner['rank'] if winner else None,
            feasible_parameters=sum(r['feasible'] for r in eligible), primary_observer='controller',
            status=('frozen' if winner['feasible'] else 'frozen_with_budget_failures') if winner else 'unavailable'))
    return scores,frozen
