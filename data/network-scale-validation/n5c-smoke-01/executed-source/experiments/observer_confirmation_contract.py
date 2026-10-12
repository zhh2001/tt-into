"""控制器目标静态确认：固定十二配置、双业务、三次独立网络启动。"""
import copy
import json
import math
import random

from observer_training_contract import ROOT, CANDIDATE_PATH, CANDIDATE_SHA256, METHOD_PATH, METHOD_SHA256, protocol, rates, LAYERS, sha

TRAINING = ROOT / 'data/observer-training-validation/n4c-static-01'
INPUTS = {
    'data/observer-training-validation/n4c-static-01/manifest.json': '6b20e311f35ef1fd13ac33239ac1d62b4831fa4fecba4818887b6d32e7431fbc',
    'data/observer-training-validation/n4c-static-01/candidate-scores.json': 'aaa691a1941d831f077a29e284eddbb2bdc110692661ea29591cba2a9592a4c5',
    'data/observer-training-validation/n4c-static-01/training-selections.json': '16c8bc67138c15edbdbfb373411340ee2f37a3e3879254a57ee58446fe7539d9',
    'data/observer-training-audit/n4c-static-01/independent-checks.json': '5ca6c0b7c51c5ffbe1a76f2ea1ee41770aba2174cc48ec53d34de6129084f264',
}
INDICES = {'stable': [2, 7, 6], 'shared_priority': [3, 2, 4], 'switch': [2, 5, 4], 'bursty_jitter': [5, 11, 1]}
ORDER_SEEDS = (42426410, 42426411, 42426412)


def shortlist():
    for name, digest in INPUTS.items():
        if sha(ROOT / name) != digest: raise ValueError('训练确认来源摘要改变: ' + name)
    scores = json.loads((TRAINING / 'candidate-scores.json').read_text())
    winners = json.loads((TRAINING / 'training-selections.json').read_text())
    groups = []
    for winner in winners:
        members = [s for s in scores if s['scenario'] == winner['scenario']]
        old = next(s for s in members if s['config'] == winner['previous_common_initialization'])
        selected = next(s for s in members if s['candidate_index'] == winner['selected_candidate'])
        challenger = min((s for s in members if s['feasible'] and s['candidate_index'] not in
                          (old['candidate_index'], selected['candidate_index'])), key=lambda s: s['rank'])
        chosen = [old, selected, challenger]
        if [s['candidate_index'] for s in chosen] != INDICES[winner['scenario']]: raise ValueError('固定确认名单改变')
        groups.append({'scenario': winner['scenario'], 'budget_level': winner['budget_level'], 'candidates': [
            {'role': role, 'candidate_index': s['candidate_index'], 'config': s['config'], 'training_rank': s['rank']}
            for role, s in zip(('previous_common_initialization', 'training_selected', 'best_other_feasible'), chosen)]})
    return {'groups': groups, 'input_sha256': INPUTS}


def expected_order():
    adaptive = json.loads(METHOD_PATH.read_text())['methods']['static']['adaptive']
    entries = []
    for block, seed_order in enumerate(ORDER_SEEDS, 1):
        rows = []
        for group in shortlist()['groups']:
            for seed in (11, 29):
                for candidate in group['candidates']:
                    rows.append({'scenario': group['scenario'], 'budget_level': group['budget_level'], 'seed': seed,
                        'candidate_index': candidate['candidate_index'], 'role': candidate['role'], 'block': block,
                        'order_seed': seed_order, 'method': 'static', 'parameter_index': None,
                        'initial_config': candidate['config'], 'adaptive': copy.deepcopy(adaptive),
                        'name': f"{group['scenario']}-{group['budget_level']}-s{seed}-c{candidate['candidate_index']:02d}-b{block}"})
        random.Random(seed_order).shuffle(rows); entries.extend(rows)
    return [dict(row, position=i) for i, row in enumerate(entries)]


def confirmation_protocol():
    value = protocol()
    value.update(confirmation_selection=['all_six_deployments_within', 'worst_controller_loss_upper',
        'mean_controller_loss_upper', 'enabled_count', 'configuration_code'], confirmation_blocks=3,
        confirmation_expected_cases=72, network_restart_per_block=True)
    return value


def plan_fields():
    return {'status': 'prepared', 'stage': 'controller_observer_static_confirmation', 'independent_test': False,
        'training_seeds': [11, 29], 'expected_cases': 72, 'deployment_blocks': 3,
        'repeats_per_candidate_business': 3, 'order_seeds': list(ORDER_SEEDS),
        'candidate_plan_sha256': CANDIDATE_SHA256, 'method_contract_sha256': METHOD_SHA256,
        'primary_observer': 'controller', 'observation_period_us': 100000, 'decision_period_us': 1000000,
        'duration_us': 10000000, 'protocol': confirmation_protocol(), 'shortlist': shortlist(),
        'execution_order': expected_order()}


def validate(plan, directory=None, current=False):
    if sha(CANDIDATE_PATH) != CANDIDATE_SHA256 or sha(METHOD_PATH) != METHOD_SHA256:
        raise ValueError('候选或方法原始来源改变')
    for key, value in plan_fields().items():
        if plan.get(key) != value: raise ValueError('共同观察确认合同不符: ' + key)
    if directory is not None:
        for name, value in (('shortlist.json', plan['shortlist']), ('protocol.json', plan['protocol']),
                            ('seed-audit.json', plan['seed_audit'])):
            if json.loads((directory / name).read_text()) != value: raise ValueError('准备副本改变: ' + name)
        for name, digest in plan['source_sha256'].items():
            if sha(directory / 'executed-source' / name) != digest: raise ValueError('源码快照改变')
        if sha(directory / 'candidate-plan.json') != CANDIDATE_SHA256 or sha(directory / 'method-contract.json') != METHOD_SHA256:
            raise ValueError('候选/方法副本改变')
    if current:
        from run_observer_confirmation import SOURCES
        if set(plan['source_sha256']) != set(SOURCES): raise ValueError('执行源码集合不完整')
        for name, digest in plan['source_sha256'].items():
            if sha(ROOT / name) != digest: raise ValueError('执行源码改变: ' + name)
    return plan['shortlist']


def select(plan, summaries):
    validate(plan)
    keys = ('scenario', 'budget_level', 'candidate_index', 'seed', 'block')
    actual = [tuple(r[k] for k in keys) for r in summaries]
    expected = {tuple(r[k] for k in keys) for r in plan['execution_order']}
    if len(actual) != 72 or len(set(actual)) != 72 or set(actual) != expected:
        raise ValueError('完整72例双业务三块分母不符')
    scores, selections = [], []
    for group in plan['shortlist']['groups']:
        eligible = []
        for c in group['candidates']:
            rows = sorted((r for r in summaries if r['scenario'] == group['scenario'] and
                          r['candidate_index'] == c['candidate_index']), key=lambda r: (r['seed'], r['block']))
            valid = all(type(r[k]) in (int, float) and math.isfinite(r[k]) for r in rows
                        for k in ('controller_loss_lower', 'controller_loss_upper'))
            valid = valid and all(0 <= r['controller_loss_lower'] <= r['controller_loss_upper'] <= 4 + 1e-10 for r in rows)
            expenses = [rates(r) for r in rows]
            known_cost = all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for d in expenses for v in d.values())
            feasible = valid and known_cost and all(r['window_feasibility'] == 'within' for r in rows)
            rank = [max(r['controller_loss_upper'] for r in rows), sum(r['controller_loss_upper'] for r in rows)/6,
                    sum(v is not None for v in c['config']), [v or 0 for v in c['config']]] if valid else None
            score = dict(c, scenario=group['scenario'], budget_level=group['budget_level'], case_count=6,
                age_evaluable=valid, feasible=feasible, rank=rank,
                worst_rates={k: max(d[k] for d in expenses) for k in LAYERS} if known_cost else None,
                window_statuses={f"{r['seed']}:b{r['block']}": r['window_feasibility'] for r in rows},
                case_names=[r['case'] for r in rows],
                controller_loss_bounds={f"{r['seed']}:b{r['block']}": [r['controller_loss_lower'], r['controller_loss_upper']] for r in rows})
            scores.append(score)
            if feasible: eligible.append(score)
        winner = min(eligible, key=lambda r: r['rank']) if eligible else None
        selections.append(dict(scenario=group['scenario'], budget_level=group['budget_level'],
            feasible_candidates=len(eligible), selected_candidate=winner['candidate_index'] if winner else None,
            selected_config=winner['config'] if winner else None, selected_rank=winner['rank'] if winner else None,
            status='frozen' if winner else 'finite_subset_infeasible', primary_observer='controller',
            observation_period_us=100000, decision_period_us=1000000))
    return scores, selections
