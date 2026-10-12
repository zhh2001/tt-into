"""有限网络静态训练的冻结候选、顺序与双种子选择合同。"""
import hashlib
import math
from pathlib import Path
import random

PROFILES = (('stable', 'high'), ('shared_priority', 'high'), ('switch', 'low'), ('bursty_jitter', 'low'))
GRID = (None, 50000, 100000, 150000, 200000, 400000, 600000, 800000, 1000000)
FROZEN_PLAN_SHA256 = '8af74004869e0b644b422a077503ae96403c52fef1b0e3d9ecf1083ac3292237'
BLOCK_ORDER_SEED = 22360679
WITHIN_ORDER_SEEDS = tuple(24494897 + i for i in range(8))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_plan(plan, directory=None, reference=None):
    if (plan['status'], plan['training_seeds'], plan['grid_us'], plan['candidates_per_cell'],
        plan['expected_network_cases'], plan['network_repetitions_per_candidate_seed']) != (
            'prepared', [11, 29], list(GRID), 24, 192, 1):
        raise ValueError('静态候选合同或训练分母错误')
    if [(g['scenario'], g['budget_level']) for g in plan['groups']] != list(PROFILES):
        raise ValueError('静态候选单元错误')
    for group in plan['groups']:
        cs = group['candidates']
        if [c['candidate_index'] for c in cs] != list(range(24)) or len({tuple(c['config']) for c in cs}) != 24:
            raise ValueError('候选数量、身份或配置重复')
        if cs[0]['config'] != [None]*4 or cs[1]['config'] != [50000]*4:
            raise ValueError('禁用或最小阈值锚点缺失')
        if not any(c['config'] == group['frozen_static'] for c in cs):
            raise ValueError('冻结模型静态参照缺失')
        if any(len(c['config']) != 4 or any(v not in GRID for v in c['config']) for c in cs):
            raise ValueError('候选配置越界')
    if directory is not None:
        for name, digest in plan['source_sha256'].items():
            if sha(Path(directory) / 'executed-source' / name) != digest:
                raise ValueError('候选生成源码摘要不同')
    if reference is not None:
        expected = dict(plan['input_sha256'], **{'manifest.json': plan['reference_manifest_sha256'],
                                               'training-selections.json': plan['training_selection_sha256']})
        for name, digest in expected.items():
            if sha(Path(reference) / name) != digest:
                raise ValueError('候选原训练来源摘要不同')


def execution_order(plan, smoke=False):
    validate_plan(plan)
    blocks = list(range(8))
    random.Random(BLOCK_ORDER_SEED).shuffle(blocks)
    result = []
    for block in blocks:
        group, seed = plan['groups'][block//2], plan['training_seeds'][block%2]
        candidates = list(range(24))
        random.Random(WITHIN_ORDER_SEEDS[block]).shuffle(candidates)
        for candidate in candidates:
            if smoke and (block != 0 or candidate not in (0, 1)):
                continue
            s, b = group['scenario'], group['budget_level']
            result.append({'name': f'b{block+1:02d}-{s}-{b}-s{seed}-c{candidate:02d}',
                'block': block+1, 'order_seed': WITHIN_ORDER_SEEDS[block], 'scenario': s,
                'budget_level': b, 'seed': seed, 'candidate_index': candidate, 'method': 'static',
                'config': group['candidates'][candidate]['config']})
    return result


def select_candidates(plan, summaries):
    """只接受完整双种子，按预声明最坏上界、均值与配置编码选择。"""
    scores, selections = [], []
    for group in plan['groups']:
        cell = (group['scenario'], group['budget_level'])
        ranked = []
        for candidate in group['candidates']:
            rs = [r for r in summaries if (r['scenario'], r['budget_level'], r['candidate_index']) == (*cell, candidate['candidate_index'])]
            if len(rs) != 2 or sorted(r['seed'] for r in rs) != [11, 29]:
                raise ValueError('静态候选双种子分母不完整')
            valid_age = all(isinstance(r[k], (int, float)) and math.isfinite(r[k]) for r in rs for k in ('loss_lower', 'loss_upper'))
            valid_age = valid_age and all(0 <= r['loss_lower'] <= r['loss_upper'] <= 4+1e-10 for r in rs)
            feasible = valid_age and all(r['window_feasibility'] == 'within' for r in rs)
            config = candidate['config']
            upper = [r['loss_upper'] for r in rs] if valid_age else None
            rank = [max(upper), sum(upper)/2, sum(v is not None for v in config), [v or 0 for v in config]] if valid_age else None
            score = {'scenario': cell[0], 'budget_level': cell[1], **candidate,
                     'feasible': feasible, 'age_evaluable': valid_age, 'rank': rank,
                     'window_statuses': {str(r['seed']): r['window_feasibility'] for r in rs},
                     'collector_loss_bounds': {str(r['seed']): [r['loss_lower'], r['loss_upper']] for r in rs},
                     'frozen_model_reference': config == group['frozen_static']}
            scores.append(score)
            if feasible:
                ranked.append(score)
        winner = min(ranked, key=lambda r: r['rank']) if ranked else None
        model = next(r for r in scores if (r['scenario'], r['budget_level']) == cell and r['frozen_model_reference'])
        selections.append({'scenario': cell[0], 'budget_level': cell[1], 'feasible_candidates': len(ranked),
            'selected_candidate': winner['candidate_index'] if winner else None,
            'selected_config': winner['config'] if winner else None, 'selected_rank': winner['rank'] if winner else None,
            'model_candidate': model['candidate_index'], 'model_config': model['config'],
            'model_feasible': model['feasible'], 'model_rank': model['rank'],
            'status': 'selected' if winner else 'finite_subset_infeasible'})
    return scores, selections
