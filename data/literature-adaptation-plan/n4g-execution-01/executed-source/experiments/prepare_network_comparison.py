"""固定独立业务比较的方法合同，不生成或消费新业务。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from dynamic_calibration_contract import validate_plan


METHODS = ('static', 'feedback', 'local', 'joint', 'phase_joint',
           'uniform_guard', 'phase_guard', 'phase_local_guard')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text())


def prepare(analysis, output):
    manifest = load(analysis / 'manifest.json')
    if manifest['status'] != 'passed' or manifest['smoke'] or len(manifest['cases']) != 184:
        raise ValueError('要求完整有限动态校准')
    names = ('calibration-plan.json', 'frozen-parameters.json', 'summary-checks.json')
    for name in names:
        if sha(analysis / name) != manifest['artifact_sha256'][name]:
            raise ValueError('校准输入摘要不同')
    if load(analysis / 'summary-checks.json')['status'] != 'passed':
        raise ValueError('校准汇总未独立通过')
    plan = load(analysis / 'calibration-plan.json')
    validate_plan(plan)
    winners = load(analysis / 'frozen-parameters.json')
    frozen = {r['method']: r for r in winners}
    if len(frozen) != 3 or set(frozen) != set(METHODS[-3:]):
        raise ValueError('缺少三方法全局冻结')
    settings = {}
    for method in METHODS:
        candidates = [e for e in plan['execution_order'] if e['method'] == method]
        if method in frozen:
            winner = frozen[method]
            if winner['status'] not in ('frozen', 'frozen_with_budget_failures'):
                raise ValueError('参数不可冻结')
            candidates = [e for e in candidates if e['parameter_index'] == winner['selected_parameter']]
        else:
            winner = {'selected_parameter': None, 'status': 'fixed_reference'}
        adaptive = candidates[0]['adaptive']
        if len(candidates) != 8 or any(e['adaptive'] != adaptive for e in candidates):
            raise ValueError('参数不是共同全局设置')
        if method in frozen and adaptive != winner['adaptive']:
            raise ValueError('冻结值与执行设置不同')
        settings[method] = {'parameter_index': winner['selected_parameter'],
                            'status': winner['status'], 'adaptive': adaptive}
    profiles = [{'scenario': s, 'budget_level': b, 'initial_config':
                 next(e['initial_config'] for e in plan['execution_order']
                      if (e['scenario'], e['budget_level']) == (s, b))}
                for s, b in (('stable', 'high'), ('shared_priority', 'high'),
                             ('switch', 'low'), ('bursty_jitter', 'low'))]
    contract = {'status': 'methods_frozen', 'execution_ready': False,
        'calibration_manifest_sha256': sha(analysis / 'manifest.json'),
        'calibration_plan_sha256': sha(analysis / 'calibration-plan.json'),
        'frozen_parameters_sha256': sha(analysis / 'frozen-parameters.json'),
        'methods': settings, 'profiles': profiles, 'settings': manifest['settings'],
        'fresh_business_count': 4, 'deployment_blocks': 2, 'expected_cases': 256,
        'fresh_business_seeds': None, 'order_seed': 42426407,
        'duration_s': 10, 'measurement_s': [2, 10], 'notice_schedule_s': [0, 5],
        'budget': {'high': {'e1': 240, 'e2': 240, 'e3': 390, 'export': 900, 'api': 1200, 'local_notice': 128},
                   'low': {'e1': 180, 'e2': 180, 'e3': 300, 'export': 540, 'api': 1200, 'local_notice': 128}},
        'primary_observer': 'collector', 'primary_metric': 'task_weighted_clipped_normalized_generation_age_loss',
        'pairing_unit': 'same_full_business_plan_and_deployment_block',
        'independent_business_units': 4,
        'reporting': ['all_cases_and_failures', 'both_within_eligibility', 'conditional_paired_differences',
                      'separate_controller_age', 'six_separate_cost_layers', 'deployment_repeat_variation'],
        'hypothesis_scope': '重点监测局部收益的迁移与四场景边界；有限方向检查，不提供一般显著性或普遍优越',
        'execution_preconditions': ['independent_runner_and_checker', 'historical_consumption_audit',
                                    'fresh_seed_list_and_complete_order', 'pre_business_source_snapshot'],
        'seed_generation_policy': '入口与检查准备后审计历史实际生成/执行业务；由3001起递增选择最小四个未消费整数，先冻结清单再生成业务',
        'scope': '方法与评价合同已冻结，业务和执行源码清单尚待准备，未生成或消费新测试业务'}
    output.mkdir(parents=True, exist_ok=False)
    (output / 'method-contract.json').write_text(json.dumps(contract, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    for name in names:
        shutil.copyfile(analysis / name, output / name)
    sources = (Path(__file__).resolve(), Path(__file__).resolve().with_name('dynamic_calibration_contract.py'))
    for source in sources:
        target = output / 'executed-source/experiments' / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    record = {'status': 'methods_frozen', 'execution_ready': False,
        'input_sha256': {n: sha(analysis / n) for n in names},
        'source_sha256': {'experiments/' + p.name: sha(p) for p in sources},
        'artifact_sha256': {str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}}
    (output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': contract['status'], 'expected_cases': 256, 'fresh_business_seeds': None}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.analysis, args.output)
