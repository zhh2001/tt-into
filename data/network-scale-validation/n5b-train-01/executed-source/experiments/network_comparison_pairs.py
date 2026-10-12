"""完整224配对及业务内两部署变异，保留双方预算资格。"""
from collections import Counter
import json

from diagnose_static_transfer import signature, load
from prepare_network_comparison import METHODS


def bounds(row, prefix=''):
    return [float(row[prefix + 'loss_' + side]) for side in ('lower', 'upper')]


def delta(a, b):
    lo, hi = a[0] - b[1], a[1] - b[0]
    return {'lower': lo, 'upper': hi, 'direction': 'lower' if hi < 0 else 'higher' if lo > 0 else 'overlap'}


def summarize_pairs(output, cases, summaries):
    rows = {r['case']: r for r in summaries}
    keys = {(c['scenario'], c['budget_level'], c['seed'], c['block'], c['method']): c for c in cases}
    signatures, workloads, views = {}, {}, {}
    for c in cases:
        directory = output / c['name']
        workload, samples = signature(directory)
        key = (c['scenario'], c['seed'])
        full = (workload, load(directory / 'tasks.json'))
        if key in workloads and workloads[key] != full:
            raise ValueError('方法或部署块未使用同一完整业务与任务')
        workloads[key] = full
        signatures[c['name']] = samples
        views[c['name']] = bounds(load(directory / 'controller-visible-bounds.json'))
    pairs = []
    for key, c in sorted(keys.items()):
        s, b, seed, block, method = key
        if method == 'static': continue
        static = keys[s, b, seed, block, 'static']
        a, z = rows[c['name']], rows[static['name']]
        pairs.append({'scenario': s, 'budget_level': b, 'seed': seed, 'block': block, 'method': method,
            'dynamic_case': c['name'], 'static_case': static['name'],
            'both_within': a['window_feasibility'] == z['window_feasibility'] == 'within',
            'dynamic_feasibility': a['window_feasibility'], 'static_feasibility': z['window_feasibility'],
            'dynamic_loss_bounds': bounds(a), 'static_loss_bounds': bounds(z),
            'collector_difference': delta(bounds(a), bounds(z)),
            'controller_difference': delta(views[c['name']], views[static['name']]),
            'position_symmetric_difference': len(set(signatures[c['name']]) ^ set(signatures[static['name']])),
            'dynamic_fee_rates': {k: a[k] for k in a if k.endswith('_bytes_per_s')},
            'static_fee_rates': {k: z[k] for k in z if k.endswith('_bytes_per_s')}})
    if len(pairs) != 224: raise ValueError('完整动态/静态配对分母错误')
    business_pairs, repeats = [], []
    for s, b, seed, method in sorted({(c['scenario'], c['budget_level'], c['seed'], c['method']) for c in cases}):
        group = sorted([c for c in cases if (c['scenario'], c['budget_level'], c['seed'], c['method']) == (s,b,seed,method)], key=lambda c:c['block'])
        if [c['block'] for c in group] != [1, 2]: raise ValueError('业务内两部署块不完整')
        losses = [bounds(rows[c['name']]) for c in group]
        signatures_pair = [signatures[c['name']] for c in group]
        repeats.append({'scenario': s, 'budget_level': b, 'seed': seed, 'method': method,
            'cases': [c['name'] for c in group], 'loss_bounds': losses,
            'same_sampling_positions': signatures_pair[0] == signatures_pair[1],
            'position_symmetric_difference': len(set(signatures_pair[0]) ^ set(signatures_pair[1])),
            'loss_intervals_overlap': max(x[0] for x in losses) <= min(x[1] for x in losses),
            'loss_midpoint_range': abs(sum(losses[0]) - sum(losses[1])) / 2})
        if method == 'static': continue
        members = [p for p in pairs if (p['scenario'],p['budget_level'],p['seed'],p['method']) == (s,b,seed,method)]
        business_pairs.append({'scenario': s, 'budget_level': b, 'seed': seed, 'method': method,
            'deployment_pairs': len(members), 'both_within_pairs': sum(p['both_within'] for p in members),
            'both_blocks_eligible_lower': all(p['both_within'] and p['collector_difference']['upper'] < 0 for p in members),
            'collector_directions': dict(Counter(p['collector_difference']['direction'] for p in members)),
            'collector_mean_difference': [sum(p['collector_difference'][side] for p in members)/2 for side in ('lower','upper')],
            'controller_mean_difference': [sum(p['controller_difference'][side] for p in members)/2 for side in ('lower','upper')]})
    groups = []
    for s,b,method in sorted({(p['scenario'],p['budget_level'],p['method']) for p in pairs}):
        members = [p for p in business_pairs if (p['scenario'],p['budget_level'],p['method']) == (s,b,method)]
        groups.append({'scenario':s,'budget_level':b,'method':method,'business_count':len(members),
            'deployment_pairs':sum(p['deployment_pairs'] for p in members),
            'both_within_pairs':sum(p['both_within_pairs'] for p in members),
            'both_blocks_eligible_lower_businesses':sum(p['both_blocks_eligible_lower'] for p in members),
            'collector_mean_difference': [sum(p['collector_mean_difference'][i] for p in members)/len(members) for i in (0,1)],
            'scope':'四个业务分列后汇总，两部署不是两个独立业务样本，条件差值不是置信区间'})
    for name,value in (('paired-cases.json',pairs),('business-pairs.json',business_pairs),
                       ('deployment-repeats.json',repeats),('paired-groups.json',groups)):
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    return {'paired_cases':len(pairs),'business_pair_groups':len(business_pairs),'deployment_repeat_groups':len(repeats),
        'both_within_pairs':sum(p['both_within'] for p in pairs),
        'all_businesses_both_blocks_eligible_lower_groups':[dict(scenario=p['scenario'],budget_level=p['budget_level'],method=p['method']) for p in groups if p['both_blocks_eligible_lower_businesses']==4],
        'max_within_business_midpoint_range':max(p['loss_midpoint_range'] for p in repeats)}
