"""共同100ms观察的有限静态训练、完整双业务与费用前沿合同。"""
import copy
import json
import math
from pathlib import Path
import random

from network_static_contract import GRID, PROFILES, validate_plan as validate_candidates
from report_observer_contract import METHOD_PATH, METHOD_SHA256, sha

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_PATH = ROOT / 'data/network-static-plan/n3e-candidates-01/candidate-plan.json'
CANDIDATE_SHA256 = '8af74004869e0b644b422a077503ae96403c52fef1b0e3d9ecf1083ac3292237'
ORDER_SEED = 42426409
LAYERS = ('e1', 'e2', 'e3', 'export', 'api_serialized', 'local_notice_payload')
METHODS = ('static', 'feedback', 'local', 'joint', 'phase_joint', 'uniform_guard', 'phase_guard', 'phase_local_guard')
PRIMARY_METRIC = 'task_weighted_clipped_normalized_generation_age_loss'


def protocol():
    return {'status': 'development_protocol_frozen', 'execution_ready': False,
        'methods': list(METHODS), 'primary_observer': 'controller', 'secondary_observer': 'collector',
        'primary_metric': PRIMARY_METRIC, 'clip': 4, 'fixed_packet_record_limit': 1,
        'observation_period_us': 100000, 'decision_period_us': 1000000,
        'duration_us': 10000000, 'warmup_us': [0, 2000000], 'measurement_window_us': [2000000, 10000000],
        'grid_us': list(GRID), 'configuration_count': 6561, 'max_candidates': 48, 'max_update_rpcs': 6,
        'training_seeds': [11, 29], 'calibration_seeds': [1301, 1423], 'calibration_expected_cases': 184,
        'guarded_parameter_count': 6, 'parameter_selection_pending': True, 'static_selection_pending': True,
        'fresh_business_seeds': None, 'fresh_inputs_generated': False, 'test_business_count': 4, 'test_deployment_blocks': 2,
        'test_expected_cases': 256, 'cost_layers': list(LAYERS), 'budget': json.loads(METHOD_PATH.read_text())['budget'],
        'training_selection': ['both_businesses_within', 'minimax_controller_loss_upper', 'mean_controller_loss_upper',
                               'enabled_count', 'configuration_code'],
        'calibration_selection': ['non_within_case_count', 'sum_normalized_excess_upper',
                                  'mean_controller_loss_upper', 'successful_updates', 'parameter_index'],
        'frontier': 'finite_subset_six_layer_worst_business_rates_and_worst_controller_loss_upper',
        'test_reporting': ['all_cases_and_failures', 'both_within_pairs', 'conditional_paired_differences',
                           'collector_and_controller_separately', 'six_separate_cost_layers', 'deployment_repeat_variation'],
        'scope': '开发协议已冻结，静态与全局参数待训练/校准；四个新测试业务待最终审计，不生成新业务'}


def expected_order(candidates):
    validate_candidates(candidates)
    adaptive = json.loads(METHOD_PATH.read_text())['methods']['static']['adaptive']
    entries = []
    for group in candidates['groups']:
        for seed in (11, 29):
            for candidate in group['candidates']:
                entries.append({'scenario': group['scenario'], 'budget_level': group['budget_level'], 'seed': seed,
                    'candidate_index': candidate['candidate_index'], 'method': 'static', 'parameter_index': None,
                    'initial_config': candidate['config'], 'adaptive': copy.deepcopy(adaptive),
                    'name': f"{group['scenario']}-{group['budget_level']}-s{seed}-c{candidate['candidate_index']:02d}"})
    random.Random(ORDER_SEED).shuffle(entries)
    return [dict(e, position=i, order_seed=ORDER_SEED) for i, e in enumerate(entries)]


def validate(plan, directory=None, current=False):
    if sha(CANDIDATE_PATH) != CANDIDATE_SHA256 or sha(METHOD_PATH) != METHOD_SHA256:
        raise ValueError('既有候选或方法来源摘要改变')
    candidates = json.loads(CANDIDATE_PATH.read_text())
    expected = {'status': 'prepared', 'stage': 'controller_observer_static_training', 'independent_test': False,
        'training_seeds': [11, 29], 'expected_cases': 192, 'repeats_per_candidate_business': 1,
        'candidate_plan_sha256': CANDIDATE_SHA256, 'method_contract_sha256': METHOD_SHA256,
        'primary_observer': 'controller', 'observation_period_us': 100000, 'decision_period_us': 1000000,
        'duration_us': 10000000, 'order_seed': ORDER_SEED, 'protocol': protocol(), 'execution_order': expected_order(candidates)}
    for key, value in expected.items():
        if plan.get(key) != value: raise ValueError('共同观察静态训练合同不符: ' + key)
    if directory is not None:
        directory = Path(directory)
        if sha(directory / 'candidate-plan.json') != CANDIDATE_SHA256 or sha(directory / 'method-contract.json') != METHOD_SHA256:
            raise ValueError('训练候选与方法副本改变')
        if json.loads((directory / 'protocol.json').read_text()) != plan['protocol']:
            raise ValueError('完整开发评价协议副本改变')
        if json.loads((directory / 'seed-audit.json').read_text()) != plan['seed_audit']:
            raise ValueError('消费审计副本改变')
        for name, digest in plan['source_sha256'].items():
            if sha(directory / 'executed-source' / name) != digest: raise ValueError('首例前源码快照改变')
    if current:
        from run_observer_training import SOURCES
        if set(plan['source_sha256']) != set(SOURCES): raise ValueError('执行源码集合不完整')
        for name, digest in plan['source_sha256'].items():
            if sha(ROOT / name) != digest: raise ValueError('首例前源码改变: ' + name)
    return candidates


def rates(row):
    return {k: row[k + '_upper_bytes_per_s'] for k in LAYERS[:4]} | {
        'api_serialized': row['api_bytes_per_s'], 'local_notice_payload': row['local_notice_bytes_per_s']}


def select(plan, summaries):
    validate(plan)
    expected = {(e['scenario'], e['budget_level'], e['seed'], e['candidate_index']) for e in plan['execution_order']}
    actual = [(r['scenario'], r['budget_level'], r['seed'], r['candidate_index']) for r in summaries]
    if len(actual) != 192 or len(set(actual)) != 192 or set(actual) != expected:
        raise ValueError('有限静态训练完整双业务分母不符')
    candidates = json.loads(CANDIDATE_PATH.read_text()); scores, selections = [], []
    old = json.loads(METHOD_PATH.read_text())['profiles']
    for group in candidates['groups']:
        cell = group['scenario'], group['budget_level']; ranking = []
        for c in group['candidates']:
            rows = [r for r in summaries if (r['scenario'], r['budget_level'], r['candidate_index']) == (*cell, c['candidate_index'])]
            valid = all(type(r[k]) in (int, float) and math.isfinite(r[k]) for r in rows for k in ('controller_loss_lower', 'controller_loss_upper'))
            valid = valid and all(0 <= r['controller_loss_lower'] <= r['controller_loss_upper'] <= 4 + 1e-10 for r in rows)
            upper = [r['controller_loss_upper'] for r in rows] if valid else None
            rank = [max(upper), sum(upper)/2, sum(v is not None for v in c['config']), [v or 0 for v in c['config']]] if valid else None
            feasible = valid and all(r['window_feasibility'] == 'within' for r in rows)
            costs = [rates(r) for r in rows]
            cost_valid = all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for d in costs for v in d.values())
            worst = {k: max(d[k] for d in costs) for k in LAYERS} if cost_valid else None
            feasible = feasible and cost_valid
            score = {'scenario': cell[0], 'budget_level': cell[1], **c, 'age_evaluable': valid, 'feasible': feasible,
                'rank': rank, 'worst_rates': worst, 'frontier_evaluable': valid and cost_valid,
                'window_statuses': {str(r['seed']): r['window_feasibility'] for r in rows},
                'controller_loss_bounds': {str(r['seed']): [r['controller_loss_lower'], r['controller_loss_upper']] for r in rows},
                'case_names': [r['case'] for r in sorted(rows, key=lambda r: r['seed'])]}
            scores.append(score)
            if feasible: ranking.append(score)
        winner = min(ranking, key=lambda r: r['rank']) if ranking else None
        prior = next(r['initial_config'] for r in old if (r['scenario'], r['budget_level']) == cell)
        selections.append({'scenario': cell[0], 'budget_level': cell[1], 'feasible_candidates': len(ranking),
            'selected_candidate': winner['candidate_index'] if winner else None, 'selected_config': winner['config'] if winner else None,
            'selected_rank': winner['rank'] if winner else None, 'previous_common_initialization': prior,
            'selection_changed': winner['config'] != prior if winner else None,
            'status': 'training_selected_pending_confirmation' if winner else 'finite_subset_infeasible'})
    for score in scores:
        vector = ([score['rank'][0]] + [score['worst_rates'][k] for k in LAYERS]) if score['frontier_evaluable'] else None
        dominators = []
        if vector is not None:
            for other in scores:
                if (other['scenario'], other['budget_level']) != (score['scenario'], score['budget_level']) or not other['frontier_evaluable']: continue
                rival = [other['rank'][0]] + [other['worst_rates'][k] for k in LAYERS]
                if all(a <= b for a, b in zip(rival, vector)) and any(a < b for a, b in zip(rival, vector)):
                    dominators.append(other['candidate_index'])
        score['frontier_vector'] = vector; score['dominated_by'] = dominators
        score['finite_subset_nondominated'] = vector is not None and not dominators
    return scores, selections
