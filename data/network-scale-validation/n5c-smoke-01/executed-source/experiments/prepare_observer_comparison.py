"""校准验收后冻结共同观察的八方法，不生成新测试业务。"""
import argparse
import json
from pathlib import Path
import shutil
from observer_calibration_contract import ROOT, sha, validate
from run_matched_reference import METHODS, GUARDED
from analyze_report_observer import write
from check_matched_network import load


def prepare(analysis, checks, output):
    manifest=load(analysis/'manifest.json');audit=load(checks)
    if manifest['status']!='passed' or len(manifest['cases'])!=184 or audit['status']!='passed' or audit['cases']!=184:
        raise ValueError('要求完整184例校准验收')
    if audit['analysis_manifest_sha256']!=sha(analysis/'manifest.json'):raise ValueError('验收没有绑定当前校准')
    names=('calibration-plan.json','frozen-parameters.json','frozen-static.json','parameter-scores.json')
    for name in names:
        if sha(analysis/name)!=manifest['artifact_sha256'][name]:raise ValueError('校准来源改变')
    plan=load(analysis/'calibration-plan.json');validate(plan)
    frozen={r['method']:r for r in load(analysis/'frozen-parameters.json')}
    if set(frozen)!=set(GUARDED):raise ValueError('缺少三个全局参数')
    methods={}
    for method in METHODS:
        index=frozen[method]['selected_parameter'] if method in GUARDED else None
        values=[e for e in plan['execution_order'] if e['method']==method and e['parameter_index']==index]
        if len(values)!=8 or any(e['adaptive']!=values[0]['adaptive'] for e in values):raise ValueError('全局设置不完整')
        if method in GUARDED and frozen[method]['status'] not in ('frozen','frozen_with_budget_failures'):
            raise ValueError('参数不能冻结')
        methods[method]=dict(parameter_index=index,adaptive=values[0]['adaptive'],
            status=frozen[method]['status'] if method in GUARDED else 'fixed_reference')
    contract=dict(status='methods_frozen',execution_ready=False,
        calibration_manifest_sha256=sha(analysis/'manifest.json'),calibration_checks_sha256=sha(checks),
        calibration_plan_sha256=sha(analysis/'calibration-plan.json'),frozen_parameters_sha256=sha(analysis/'frozen-parameters.json'),
        methods=methods,profiles=[dict(scenario=r['scenario'],budget_level=r['budget_level'],initial_config=r['selected_config'])
            for r in plan['common_initialization']],settings=plan['settings'],budget=plan['protocol']['budget'],
        primary_observer='controller',secondary_observer='collector',primary_metric=plan['protocol']['primary_metric'],
        observation_period_us=100000,decision_period_us=1000000,duration_s=10,measurement_s=[2,10],notice_schedule_s=[0,5],
        fresh_business_seeds=None,fresh_inputs_generated=False,fresh_business_count=4,deployment_blocks=2,expected_cases=256,
        order_seed=42426414,pairing_unit='same_full_business_plan_and_deployment_block',independent_business_units=4,
        reporting=['all_cases_and_failures','both_within_pairs','conditional_paired_differences',
                   'controller_and_collector_separately','six_separate_cost_layers','deployment_repeat_variation'],
        seed_generation_policy='在执行入口完成后重新审计实际消费，从3001起取最小四个未消费整数；冻结后生成业务',
        execution_preconditions=['independent_256_case_runner_and_checker','fresh_seed_consumption_audit',
                                 'complete_order_and_pre_business_source_snapshot'],
        scope='八方法及控制器目标已冻结，四条独立业务和执行清单待下一阶段准备')
    output.mkdir(parents=True,exist_ok=False);write(output/'method-contract.json',contract)
    for name in names:shutil.copyfile(analysis/name,output/name)
    shutil.copyfile(checks,output/'calibration-independent-checks.json')
    sources=('experiments/prepare_observer_comparison.py','experiments/observer_calibration_contract.py')
    for name in sources:
        target=output/'executed-source'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    write(output/'manifest.json',dict(status='methods_frozen',execution_ready=False,business_generated=False,
        input_sha256={n:sha(analysis/n) for n in names},source_sha256={n:sha(ROOT/n) for n in sources},
        artifact_sha256={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}))
    print(json.dumps(dict(status='methods_frozen',expected_cases=256,fresh_business_seeds=None)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('analysis','checks','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();prepare(a.analysis,a.checks,a.output)
