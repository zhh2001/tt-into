"""文献训练、校准及十方法比较的独立执行合同。"""
import copy
import json
import random
from pathlib import Path

from literature_contract import ROOT, REFERENCE, REFERENCE_SHA256, sha
from prepare_literature_comparison import protocol, training_order

PREPARATION = ROOT / 'data/literature-adaptation-plan/n4e-comparison-01'
STAGES = ('training', 'calibration', 'comparison')


def specification():
    manifest = json.loads((PREPARATION / 'manifest.json').read_text())
    for name, digest in manifest['artifact_sha256'].items():
        if sha(PREPARATION / name) != digest: raise ValueError('已准备文献矩阵改变')
    spec = json.loads((PREPARATION / 'protocol.json').read_text())
    if spec != protocol() or sha(REFERENCE) != REFERENCE_SHA256:
        raise ValueError('预声明网格或共同方法来源改变')
    return spec


def order(stage, spec, selected=None, seeds=None):
    reference = json.loads(REFERENCE.read_text())
    common = reference['methods']['phase_joint']['adaptive']
    if stage == 'training':
        return [dict(e, adaptive=copy.deepcopy(common), parameter_index=e['preset_index'], candidate_index=None,
                     scope='文献适配全局参数训练') for e in training_order(spec)]
    if stage not in ('calibration', 'comparison') or selected is None:
        raise ValueError('下一阶段需要已冻结参数')
    members = []
    blocks = (1,) if stage == 'calibration' else (1, 2)
    businesses = spec['calibration']['seeds'] if stage == 'calibration' else seeds
    if stage == 'comparison' and (businesses is None or len(set(businesses)) != 4):
        raise ValueError('比较必须四条独立新业务')
    for block in blocks:
        group = []
        for profile in spec['profiles']:
            for seed in businesses:
                methods = spec['literature_methods'] if stage == 'calibration' else spec['performance']['methods']
                for method in methods:
                    indices = selected[method] if method in selected else [None]
                    for index in indices:
                        preset = reference['methods'].get(method, {})
                        group.append(dict(copy.deepcopy(profile), method=method, seed=seed, block=block,
                            preset_index=index, parameter_index=index if method in selected else preset['parameter_index'],
                            candidate_index=None, adaptive=copy.deepcopy(preset.get('adaptive', common)),
                            literature=spec['parameter_grids'][method][index] if method in selected else None,
                            scope='文献全局校准' if stage == 'calibration' else '十方法独立业务比较',
                            name=f"{profile['scenario']}-{profile['budget_level']}-s{seed}-b{block}-{method}-p{index}"))
        random.Random(42426418 if stage == 'calibration' else 42426418 + block).shuffle(group)
        members.extend(group)
    return [dict(e, position=i) for i, e in enumerate(members)]


def fields(stage, selection=None, seed_audit=None):
    spec = specification()
    selected = None if selection is None else selection['selected_indices']
    expected_status = 'training_candidates_frozen' if stage == 'calibration' else 'literature_parameters_frozen'
    if stage != 'training' and (selection is None or selection['status'] != expected_status or not selection['global_parameters']):
        raise ValueError('前一阶段全局参数未冻结')
    if stage != 'training':
        count = 2 if stage == 'calibration' else 1
        if set(selected) != set(spec['literature_methods']) or any(len(set(v)) != count for v in selected.values()):
            raise ValueError('全局候选分母不符')
        for method, values in selected.items():
            if any(type(i) is not int or not 0 <= i < 6 for i in values): raise ValueError('参数索引不在预设网格')
    seeds = spec[stage]['seeds'] if stage != 'comparison' else seed_audit['selected_fresh_seeds']
    if stage == 'comparison':
        excluded = set(seed_audit['excluded_seeds'])
        fresh = [i for i in range(3001, max(seeds) + 1) if i not in excluded][:4]
        if seeds != fresh or set(seeds) & set(range(3001, 3009)): raise ValueError('新业务审计不符')
    return dict(status='prepared', execution_ready=True, stage=stage, protocol=spec,
        business_seeds=seeds, fresh_business_seeds=seeds if stage == 'comparison' else [],
        independent_test=stage == 'comparison', fresh_inputs_generated=False,
        expected_cases=spec[stage]['cases'] if stage != 'comparison' else spec['performance']['cases'],
        deployment_blocks=2 if stage == 'comparison' else 1,
        observation_period_us=100000, decision_period_us=1000000,
        selection=selection, seed_audit=seed_audit, reference_contract_sha256=REFERENCE_SHA256,
        execution_order=order(stage, spec, selected, seeds))


def validate(plan, current=False):
    stage = plan['stage']
    if stage not in STAGES: raise ValueError('未知文献执行阶段')
    expected = fields(stage, plan['selection'], plan['seed_audit'])
    for name, value in expected.items():
        if plan.get(name) != value: raise ValueError('执行合同不符: ' + name)
    if len(plan['execution_order']) != plan['expected_cases']: raise ValueError('矩阵分母不符')
    if current:
        from run_literature_matrix import SOURCES
        if set(plan['source_sha256']) != set(SOURCES): raise ValueError('实际源码集合不完整')
        for name, digest in plan['source_sha256'].items():
            if sha(ROOT / name) != digest: raise ValueError('首例前源码改变: ' + name)
        if stage == 'comparison':
            from network_comparison_contract import audit_consumption
            actual = audit_consumption(ROOT / 'data')
            if set(plan['business_seeds']) & set(actual['excluded_seeds']): raise ValueError('冻结后新业务已消费')
    return expected
