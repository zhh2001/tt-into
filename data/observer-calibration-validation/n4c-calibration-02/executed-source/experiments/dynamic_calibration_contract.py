"""固定共同初始化、有限参数矩阵、完整分母与全局选择。"""
import itertools
import math
from pathlib import Path
import random

from confirmation_contract import PROFILES, sha
from run_matched_reference import AdaptiveSettings, METHODS, GUARDED, PARAMETERS, plain, settings
from rolling_workloads import budget_for

FROZEN_PLAN_SHA256 = '945791b3d148d497b06cb9acbe7c21699bd16df0d7a458892575fe1f7416fc0f'
ORDER_SEED = 42426406
LAYERS = ('e1', 'e2', 'e3', 'export', 'api_serialized', 'local_notice_payload')


def expected_order(plan):
    rows = []
    for cell in plan['common_initialization']:
        for seed in (1301, 1423):
            for method in METHODS:
                for index in (range(6) if method in GUARDED else (None,)):
                    adaptive = plain(PARAMETERS[index] if index is not None else AdaptiveSettings())
                    suffix = 'fixed' if index is None else f'p{index:02d}'
                    rows.append(dict(method=method, parameter_index=index, adaptive=adaptive,
                        scenario=cell['scenario'], budget_level=cell['budget_level'], seed=seed,
                        initial_config=cell['selected_config'],
                        name=f"{cell['scenario']}-{cell['budget_level']}-s{seed}-{method}-{suffix}"))
    random.Random(ORDER_SEED).shuffle(rows)
    return [dict(r, position=i, order_seed=ORDER_SEED) for i, r in enumerate(rows)]


def validate_plan(plan, directory=None, analysis=None):
    if (plan['status'], plan['calibration_seeds'], plan['expected_cases'], plan['cases_per_guarded_parameter'],
        plan['repeats_per_setting_business'], plan['fresh_test_seeds_generated'], plan['methods']) != (
            'prepared', [1301, 1423], 184, 8, 1, False, list(METHODS)):
        raise ValueError('动态校准分母、参数或业务合同错误')
    if [(r['scenario'], r['budget_level']) for r in plan['common_initialization']] != list(PROFILES) \
            or any(r['status'] != 'frozen' for r in plan['common_initialization']):
        raise ValueError('共同初始化未冻结')
    if plan['settings'] != plain(settings()) or plan['fixed_packet_record_limit'] != 1:
        raise ValueError('共同配置、搜索或每包容量改变')
    if (plan['duration_us'], plan['warmup_us'], plan['measurement_window_us'], plan['notification_offsets_us']) != (
            10000000, [0, 2000000], [2000000, 10000000], [0, 5000000]):
        raise ValueError('测量窗口或通知改变')
    profiles = [dict(scenario=s, budget_level=b, budget=plain(budget_for(s, b)), notice_payload_bytes_per_s=128) for s, b in PROFILES]
    if plan['profiles'] != profiles or plan['order_seed'] != ORDER_SEED or plan['execution_order'] != expected_order(plan):
        raise ValueError('预算、参数或冻结执行顺序改变')
    expected_presets = [dict(method=m, parameter_index=i, adaptive=plain(PARAMETERS[i] if i is not None else AdaptiveSettings()))
                        for m in METHODS for i in (range(6) if m in GUARDED else (None,))]
    if plan['presets'] != expected_presets or plan['selection']['layers'] != list(LAYERS):
        raise ValueError('参数族或费用层改变')
    if directory is not None:
        for name, digest in plan['source_sha256'].items():
            if sha(Path(directory) / 'executed-source' / name) != digest:
                raise ValueError('清单源码来源摘要不同')
    if analysis is not None:
        if sha(Path(analysis) / 'manifest.json') != plan['analysis_manifest_sha256']:
            raise ValueError('静态确认分析来源摘要不同')
        for name, digest in plan['input_sha256'].items():
            if sha(Path(analysis) / name) != digest:
                raise ValueError('共同初始化来源摘要不同')


def upper_rates(cost):
    values = cost['measurement_upper_bytes']
    if values is None:
        return None
    return {**{k: values[k] / 8 for k in LAYERS[:4]}, 'api_serialized': cost['api_bytes_per_s'],
            'local_notice_payload': cost['local_notice_bytes_per_s']}


def normalized_excess(rates, scenario, level):
    budget = budget_for(scenario, level)
    limits = dict(budget.link_bytes_per_s, export=budget.export_bytes_per_s,
                  api_serialized=budget.control_bytes_per_s, local_notice_payload=128)
    if rates is None or set(rates) != set(limits) or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in rates.values()):
        return None
    return sum(max(rates[k] / limits[k] - 1, 0) for k in LAYERS)


def select_parameters(plan, summaries):
    validate_plan(plan)
    keys = ('scenario', 'budget_level', 'seed', 'method', 'parameter_index')
    expected = {tuple(e[k] for k in keys) for e in plan['execution_order']}
    actual = [tuple(r[k] for k in keys) for r in summaries]
    if len(actual) != 184 or len(set(actual)) != 184 or set(actual) != expected:
        raise ValueError('动态校准评分完整八例分母错误')
    scores, frozen = [], []
    for method in GUARDED:
        group = []
        for index in range(6):
            rs = [r for r in summaries if (r['method'], r['parameter_index']) == (method, index)]
            valid = all(isinstance(r[k], (int, float)) and math.isfinite(r[k]) for r in rs for k in ('loss_lower', 'loss_upper', 'normalized_excess_upper'))
            valid = valid and all(0 <= r['loss_lower'] <= r['loss_upper'] <= 4 + 1e-10 and r['normalized_excess_upper'] >= 0 for r in rs)
            rank = [sum(r['window_feasibility'] != 'within' for r in rs), sum(r['normalized_excess_upper'] for r in rs),
                    sum(r['loss_upper'] for r in rs) / 8, sum(r['successful_updates'] for r in rs), index] if valid else None
            score = dict(method=method, parameter_index=index, adaptive=plain(PARAMETERS[index]), case_count=8,
                evaluable=valid, feasible=valid and rank[0] == 0, rank=rank,
                case_names=[r['case'] for r in rs], window_statuses={r['case']: r['window_feasibility'] for r in rs})
            scores.append(score)
            if valid: group.append(score)
        winner = min(group, key=lambda r: r['rank']) if group else None
        frozen.append(dict(method=method, selected_parameter=winner['parameter_index'] if winner else None,
            adaptive=winner['adaptive'] if winner else None, selected_rank=winner['rank'] if winner else None,
            feasible_parameters=sum(r['feasible'] for r in group),
            status=('frozen' if winner['feasible'] else 'frozen_with_budget_failures') if winner else 'unavailable'))
    return scores, frozen
