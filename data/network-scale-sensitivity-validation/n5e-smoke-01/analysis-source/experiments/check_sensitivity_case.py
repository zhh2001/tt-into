"""核对条件额度、真实观察周期和十次决策映射，再复用逐帧与合法来源检查。"""
from check_scale_comparison_case import check_case as check_wire_case
from check_observation_wire import load, rows
from check_rolling_reference import same
from sensitivity_execution import read_scope, condition_budget, METHODS
from orchestration.network_spec import FixedNetwork


def check_condition(case, settings, observations, decisions):
    scope, topology = read_scope()
    condition = next((c for c in scope['conditions'] if c['condition_id'] == case['condition_id']), None)
    if condition is None or case['method'] not in METHODS:
        raise ValueError('未冻结条件或方法')
    checks = 0
    for k, value in condition.items():
        same(case[k], value, '实际条件身份及周期'); checks += 1
    for k, value in scope['cell'].items():
        same(case[k], value, '全部条件共同初始化'); checks += 1
    same(case['topology'], topology, '固定五节点四流'); checks += 1
    budget = condition_budget(FixedNetwork(topology), condition['data_budget_multiplier'])
    same(case['budget'], budget, '仅带内及导出预算乘数')
    same(settings['budget'], budget, '实际规划器预算')
    same(settings['adaptive'], scope['method_parameters'][case['method']]['adaptive'], '原全局参数')
    same(settings['notice_budget_bytes_per_s'], 128, '通知额度不缩放')
    same(case['duration_us'], 10000000, '真实时长')
    same(case['decision_period_us'], 1000000, '一秒编排')
    same(settings['decision_period_us'], 1000000, '规划入口编排周期')
    same(settings['observation_period_us'], condition['observation_period_us'], '规划入口报告周期')
    period = condition['observation_period_us']
    same(len(observations), 10000000 // period, '完整实际周期槽分母')
    same([o['scheduled_offset_us'] for o in observations], list(range(0, 10000000, period)), '真实观察槽位置')
    due = [o for o in observations if o['decision_due']]
    same([o['observation_index'] for o in due], list(range(0, 10000000 // period, 1000000 // period)), '十周期映射真实槽')
    same([d['scheduled_offset_us'] for d in decisions], list(range(0, 10000000, 1000000)), '决策不随报告tick增加')
    return checks + 12


def check_case(directory, rpc):
    checks = check_condition(load(directory / 'result.json'), load(directory / 'algorithm-settings.json'),
                             rows(directory / 'report-observations.jsonl'), rows(directory / 'decisions.jsonl'))
    result = check_wire_case(directory, rpc)
    result['checks'] += checks
    result['condition_checks'] = checks
    return result
