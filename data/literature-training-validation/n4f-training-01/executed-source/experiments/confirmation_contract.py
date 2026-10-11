"""六组有限静态配置的三块重复、来源及冻结选择合同。"""
import math
from pathlib import Path
import random

from network_static_contract import PROFILES, sha

FROZEN_PLAN_SHA256 = '44017effad9f7e03eb90d74c024bbf449147dcef6441586db0774d3edaf146a8'
ORDER_SEEDS = (17320508, 26457513, 31622777)
CELLS = (
    ('stable', 'high', 2, [150000, 400000, 200000, 50000], ['frozen_model_reference', 'network_selected']),
    ('shared_priority', 'high', 2, [600000, 50000, 400000, 150000], ['frozen_model_reference']),
    ('shared_priority', 'high', 3, [600000, 100000, 400000, 150000], ['network_selected']),
    ('switch', 'low', 2, [400000, 150000, 150000, 400000], ['frozen_model_reference', 'network_selected']),
    ('bursty_jitter', 'low', 1, [50000, 50000, 50000, 50000], ['frozen_model_reference']),
    ('bursty_jitter', 'low', 5, [50000, 50000, 50000, 400000], ['network_selected']),
)


def frozen_cells():
    return [dict(scenario=s, budget_level=b, candidate_index=i, config=c, roles=r) for s, b, i, c, r in CELLS]


def _order(cells):
    result = []
    for block, order_seed in enumerate(ORDER_SEEDS, 1):
        batch = [dict(c, seed=seed) for c in cells for seed in (11, 29)]
        random.Random(order_seed).shuffle(batch)
        result.extend(dict(c, block=block, order_seed=order_seed,
            name=f"b{block:02d}-{c['scenario']}-{c['budget_level']}-s{c['seed']}-c{c['candidate_index']:02d}") for c in batch)
    return result


def validate_plan(plan, directory=None, analysis=None, diagnostic=None):
    if (plan['status'], plan['business_seeds'], plan['blocks'], plan['order_seeds'], plan['expected_cases']) != (
            'prepared', [11, 29], 3, list(ORDER_SEEDS), 36):
        raise ValueError('静态确认分母或重复合同错误')
    if plan['unique_config_cells'] != frozen_cells() or plan['execution_order'] != _order(frozen_cells()):
        raise ValueError('确认配置、角色或冻结顺序错误')
    if directory is not None:
        for name, digest in plan['source_sha256'].items():
            if sha(Path(directory) / 'executed-source' / name) != digest:
                raise ValueError('确认生成源码摘要不同')
    if analysis is not None:
        expected = dict(plan['input_sha256'], **{'manifest.json': plan['analysis_manifest_sha256']})
        for name, digest in expected.items():
            if sha(Path(analysis) / name) != digest:
                raise ValueError('原静态分析来源摘要不同')
    if diagnostic is not None and sha(Path(diagnostic) / 'manifest.json') != plan['diagnostic_manifest_sha256']:
        raise ValueError('迁移诊断来源摘要不同')


def execution_order(plan):
    validate_plan(plan)
    return [dict(r, method='static') for r in plan['execution_order']]


def select_confirmation(plan, summaries):
    """每组必须完整六例，费用和年龄均可接受才进入最坏上界排序。"""
    validate_plan(plan)
    fields = ('scenario', 'budget_level', 'candidate_index', 'seed', 'block')
    expected = {tuple(e[k] for k in fields) for e in plan['execution_order']}
    actual = [tuple(r[k] for k in fields) for r in summaries]
    if len(actual) != 36 or len(set(actual)) != 36 or set(actual) != expected:
        raise ValueError('确认评分完整六例分母错误')
    scores, selections = [], []
    for cell in plan['unique_config_cells']:
        identity = cell['scenario'], cell['budget_level'], cell['candidate_index']
        rs = [r for r in summaries if tuple(r[k] for k in fields[:3]) == identity]
        valid = all(isinstance(r[k], (int, float)) and math.isfinite(r[k]) for r in rs for k in ('loss_lower', 'loss_upper'))
        valid = valid and all(0 <= r['loss_lower'] <= r['loss_upper'] <= 4 + 1e-10 for r in rs)
        feasible = valid and all(r['window_feasibility'] == 'within' for r in rs)
        cfg = cell['config']
        rank = [max(r['loss_upper'] for r in rs), sum(r['loss_upper'] for r in rs) / 6,
                sum(v is not None for v in cfg), [v or 0 for v in cfg]] if valid else None
        scores.append(dict(cell, case_count=6, feasible=feasible, age_evaluable=valid, rank=rank,
            window_statuses={f"{r['seed']}:{r['block']}": r['window_feasibility'] for r in rs},
            collector_loss_bounds={f"{r['seed']}:{r['block']}": [r['loss_lower'], r['loss_upper']] for r in rs}))
    for s, b in PROFILES:
        eligible = [r for r in scores if (r['scenario'], r['budget_level']) == (s, b) and r['feasible']]
        winner = min(eligible, key=lambda r: r['rank']) if eligible else None
        selections.append(dict(scenario=s, budget_level=b, feasible_candidates=len(eligible),
            selected_candidate=winner['candidate_index'] if winner else None,
            selected_config=winner['config'] if winner else None,
            selected_rank=winner['rank'] if winner else None,
            status='frozen' if winner else 'finite_subset_infeasible'))
    return scores, selections
