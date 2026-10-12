"""从冻结清单、实际业务和原始帧独立核对有限动态网络校准。"""
import argparse
import json
from pathlib import Path

from check_matched_network import load, rows, sha
from check_network_calibration import check_case
from check_rolling_reference import same
from dynamic_calibration_contract import expected_order, validate_plan, FROZEN_PLAN_SHA256


def audit(run):
    m = load(run / 'manifest.json')
    same(m['status'], 'passed', '完整网络运行')
    plan = load(run / 'calibration-plan.json')
    validate_plan(plan)
    same(sha(run / 'calibration-plan.json'), m['calibration_plan_sha256'], '候选输入摘要')
    same(m['calibration_plan_sha256'], FROZEN_PLAN_SHA256, '首例前冻结候选摘要')
    order = expected_order(plan)
    same(m['execution_order'], order, '预声明动态校准顺序')
    same(load(run / 'execution-order.json'), order, '执行前顺序文件')
    same(len(m['cases']), len(order), '完整案例分母')
    same(m['expected_cases'], 184, '动态校准范围')
    same(m['smoke'], False, '完整确认而非试运行')
    frozen = load(run / 'frozen-inputs.json')
    same(list(frozen), [r['name'] for r in order], '冻结输入完整顺序')
    for i, (c, e) in enumerate(zip(m['cases'], order)):
        for key in ('name', 'order_seed', 'scenario', 'budget_level', 'seed', 'parameter_index', 'method'):
            same(c[key], e[key], '实际候选及业务身份')
        same(c['initial_config'], e['initial_config'], '实际安装冻结候选')
        same(c['position'], i, '实际执行位置')
        same(load(run / c['name'] / 'result.json'), c, '逐案例结果与根清单')
        for name, field in (('packets.json', 0), ('notice-schedule.json', 1), ('tasks.json', 2)):
            same(load(run / c['name'] / name), frozen[c['name']]['inputs'][field], '完整实际业务和任务')
        options = load(run / c['name'] / 'algorithm-settings.json')
        same(options['adaptive'], e['adaptive'], '实际执行完整参数')
        same(options['network_parameter_candidate'], e['parameter_index'], '真实参数索引')
        same(options['model_parameters_frozen'], False, '本轮网络校准参数来源')
        if c['method'] == 'static' and (c['successful_updates'] or any(d['writes'] or d['reads'] for d in rows(run/c['name']/'decisions.jsonl') if 'view' in d)):
            raise ValueError('静态参照存在在线写入或源计数读取')
        host = load(run / c['name'] / 'host.json')
        if set(host['usage']) != {'controller', 'switch_1', 'switch_2', 'switch_3', 'switch_4'}:
            raise ValueError('逐案例 CPU/内存来源缺失')
    for name, digest in m['source_sha256'].items():
        same(sha(run/'executed-source'/name), digest, '执行源码快照')
    for name, digest in m['artifact_sha256'].items():
        same(sha(run/name), digest, '完整产物摘要')
    rpc = rows(run/'rpc-operations.jsonl')
    results = [check_case(run/c['name'], rpc) for c in m['cases']]
    if any(r['status'] != 'passed' for r in results):
        raise ValueError('实际业务或控制检查失败')
    return {'status': 'passed', 'cases': results,
            'packets': sum(r['packet_count'] for r in results), 'reports': sum(r['report_count'] for r in results),
            'decisions': sum(r['decision_count'] for r in results),
            'checks': sum(r['checks']+r['live_control_checks'] for r in results),
            'availability_checks': sum(r['availability_checks'] for r in results)}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    result = audit(p.parse_args().run)
    print(json.dumps({k:v for k,v in result.items() if k != 'cases'}, ensure_ascii=False))
