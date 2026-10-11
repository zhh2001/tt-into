"""共同观察静态确认的三块重复及旧配置配对。"""
from analyze_observer_training import write
from check_matched_network import load
from diagnose_observer_training import sample_positions, difference


def summarize(output, cases):
    signatures, workloads = {}, {}
    for c in cases:
        business, positions = sample_positions(output / c['name'])
        key = c['scenario'], c['seed']
        complete = business, load(output / c['name'] / 'tasks.json')
        if key in workloads and workloads[key] != complete: raise ValueError('共同业务或任务不同')
        workloads[key] = complete
        signatures[c['name']] = positions
    write(output / 'sample-signatures.json', signatures)
    groups, pairs = [], []
    for scenario, candidate, seed in sorted({(c['scenario'], c['candidate_index'], c['seed']) for c in cases}):
        rs = sorted([c for c in cases if (c['scenario'], c['candidate_index'], c['seed']) == (scenario, candidate, seed)], key=lambda c:c['block'])
        if [c['block'] for c in rs] != [1,2,3]: raise ValueError('三块重复组不完整')
        record = dict(scenario=scenario, budget_level=rs[0]['budget_level'], candidate_index=candidate, seed=seed,
            cases=[c['name'] for c in rs], same_sample_positions=all(signatures[c['name']]==signatures[rs[0]['name']] for c in rs),
            sample_counts=[len(signatures[c['name']]) for c in rs])
        for observer, name in (('collector','generation-bounds.json'),('controller','controller-visible-bounds.json')):
            bounds = [[load(output/c['name']/name)['loss_'+k] for k in ('lower','upper')] for c in rs]
            mids = [(a+b)/2 for a,b in bounds]
            record[observer+'_bounds'] = bounds
            record[observer+'_common_overlap'] = max(a for a,b in bounds) <= min(b for a,b in bounds)
            record[observer+'_midpoint_range'] = max(mids)-min(mids)
        groups.append(record)
    for group in load(output / 'shortlist.json')['groups']:
        old = group['candidates'][0]
        for alternative in group['candidates'][1:]:
            for seed in (11,29):
                for block in (1,2,3):
                    def find(index): return next(c for c in cases if (c['scenario'],c['candidate_index'],c['seed'],c['block'])==(group['scenario'],index,seed,block))
                    a,b = find(alternative['candidate_index']),find(old['candidate_index'])
                    record = dict(scenario=group['scenario'],budget_level=group['budget_level'],seed=seed,block=block,
                        previous_case=b['name'],alternative_case=a['name'],alternative_role=alternative['role'],
                        same_sample_positions=signatures[a['name']]==signatures[b['name']],
                        both_within=all(load(output/c['name']/'window-costs.json')['window_feasibility']=='within' for c in (a,b)))
                    for observer,name in (('collector','generation-bounds.json'),('controller','controller-visible-bounds.json')):
                        d=difference(load(output/a['name']/name),load(output/b['name']/name))
                        record[observer+'_difference_bounds']=[d['lower'],d['upper']]
                        record[observer+'_direction']=d['direction']
                    pairs.append(record)
    write(output/'seed-repeat-diagnostics.json',groups)
    write(output/'candidate-repeat-pairs.json',pairs)
    return {'configuration_business_groups':len(groups), 'same_positions_groups':sum(r['same_sample_positions'] for r in groups),
        'collector_common_overlap_groups':sum(r['collector_common_overlap'] for r in groups),
        'controller_common_overlap_groups':sum(r['controller_common_overlap'] for r in groups),
        'max_controller_midpoint_range':max(r['controller_midpoint_range'] for r in groups),
        'candidate_pairs':len(pairs), 'both_within_pairs':sum(r['both_within'] for r in pairs),
        'same_positions_pairs':sum(r['same_sample_positions'] for r in pairs),
        'controller_directions':{d:sum(r['controller_direction']==d for r in pairs) for d in ('lower','higher','overlap')}}
