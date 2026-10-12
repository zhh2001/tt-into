"""四条业务各两部署，完整保留两观察位置及预算资格。"""
from collections import Counter
from check_matched_network import load, rows
from check_rolling_reference import same
from diagnose_observer_training import business_packet
from observer_comparison_contract import LAYERS


def fee_rates(row):
    value={k:[row[k+'_'+s+'_bytes_per_s'] for s in ('lower','upper')] for k in LAYERS[:4]}
    value.update(api_serialized=[row['api_bytes_per_s']]*2,local_notice_payload=[row['local_notice_bytes_per_s']]*2)
    return value


def mean_bounds(values):
    return [sum(v[i] for v in values)/len(values) if all(v[i] is not None for v in values) else None for i in (0,1)]


def records(table, signatures, decision_signatures, seeds):
    keys=('scenario','budget_level','seed','block','method')
    indexed={tuple(r[k] for k in keys):r for r in table}
    if len(table)!=256 or len(indexed)!=256 or set(r['seed'] for r in table)!=set(seeds):
        raise ValueError('完整256例身份错误')
    pairs=[]
    for key,d in sorted(indexed.items()):
        if d['method']=='static': continue
        b=indexed[key[:-1]+('static',)]
        p={k:d[k] for k in keys}
        p.update(dynamic_case=d['case'],static_case=b['case'],parameter_index=d['parameter_index'],
            dynamic_feasibility=d['window_feasibility'],static_feasibility=b['window_feasibility'],
            both_within=d['window_feasibility']==b['window_feasibility']=='within',
            same_sample_positions=signatures[d['case']]==signatures[b['case']],
            position_symmetric_difference=len(set(map(tuple,signatures[d['case']]))^set(map(tuple,signatures[b['case']]))),
            dynamic_fee_rates=fee_rates(d),static_fee_rates=fee_rates(b))
        for observer in ('controller','collector'):
            a=[d[observer+'_loss_'+s] for s in ('lower','upper')]
            z=[b[observer+'_loss_'+s] for s in ('lower','upper')]
            delta=[a[0]-z[1],a[1]-z[0]]
            p[observer+'_dynamic_bounds']=a; p[observer+'_static_bounds']=z
            p[observer+'_difference_bounds']=delta
            p[observer+'_direction']='lower' if delta[1]<0 else 'higher' if delta[0]>0 else 'overlap'
            p[observer+'_midpoint_relative_reduction']=(sum(z)-sum(a))/sum(z) if sum(z)>0 else None
        pairs.append(p)
    businesses,repeats=[],[]
    for key in sorted({(r['scenario'],r['budget_level'],r['seed'],r['method']) for r in table}):
        scenario,level,seed,method=key
        members=[indexed[scenario,level,seed,block,method] for block in (1,2)]
        names=[r['case'] for r in members]
        repeat=dict(zip(('scenario','budget_level','seed','method'),key))
        repeat.update(cases=names,same_sampling_positions=signatures[names[0]]==signatures[names[1]],
            position_symmetric_difference=len(set(map(tuple,signatures[names[0]]))^set(map(tuple,signatures[names[1]]))),
            same_decision_sequence=decision_signatures[names[0]]==decision_signatures[names[1]],
            window_statuses=[r['window_feasibility'] for r in members])
        for observer in ('controller','collector'):
            bounds=[[r[observer+'_loss_'+s] for s in ('lower','upper')] for r in members]
            repeat[observer+'_loss_bounds']=bounds
            repeat[observer+'_intervals_overlap']=max(v[0] for v in bounds)<=min(v[1] for v in bounds)
            repeat[observer+'_midpoint_range']=abs(sum(bounds[0])-sum(bounds[1]))/2
        repeats.append(repeat)
        if method=='static': continue
        values=[p for p in pairs if (p['scenario'],p['budget_level'],p['seed'],p['method'])==key]
        if len(values)!=2: raise ValueError('两部署业务配对不完整')
        business=dict(zip(('scenario','budget_level','seed','method'),key))
        business.update(deployment_pairs=2,both_within_pairs=sum(p['both_within'] for p in values),
            dynamic_cases=[p['dynamic_case'] for p in values],static_cases=[p['static_case'] for p in values])
        for observer in ('controller','collector'):
            for kind in ('dynamic_bounds','static_bounds','difference_bounds'):
                business[observer+'_mean_'+kind]=mean_bounds([p[observer+'_'+kind] for p in values])
            a,z=business[observer+'_mean_dynamic_bounds'],business[observer+'_mean_static_bounds']
            business[observer+'_midpoint_relative_reduction']=(sum(z)-sum(a))/sum(z) if sum(z)>0 else None
            business[observer+'_both_blocks_eligible_lower']=all(p['both_within'] and p[observer+'_direction']=='lower' for p in values)
            business[observer+'_directions']=dict(Counter(p[observer+'_direction'] for p in values))
        businesses.append(business)
    groups=[]
    for scenario,level,method in sorted({(p['scenario'],p['budget_level'],p['method']) for p in pairs}):
        values=[p for p in pairs if (p['scenario'],p['budget_level'],p['method'])==(scenario,level,method)]
        members=[p for p in businesses if (p['scenario'],p['budget_level'],p['method'])==(scenario,level,method)]
        if len(values)!=8 or sorted(p['seed'] for p in members)!=sorted(seeds): raise ValueError('四条独立业务不完整')
        g=dict(scenario=scenario,budget_level=level,method=method,business_count=4,deployment_pairs=8,
            both_within_pairs=sum(p['both_within'] for p in values),
            same_sample_position_pairs=sum(p['same_sample_positions'] for p in values))
        for observer in ('controller','collector'):
            for kind in ('dynamic_bounds','static_bounds','difference_bounds'):
                g[observer+'_mean_'+kind]=mean_bounds([p[observer+'_mean_'+kind] for p in members])
            a,z=g[observer+'_mean_dynamic_bounds'],g[observer+'_mean_static_bounds']
            g[observer+'_midpoint_relative_reduction']=(sum(z)-sum(a))/sum(z) if sum(z)>0 else None
            g[observer+'_directions']=dict(Counter(p[observer+'_direction'] for p in values))
            g[observer+'_both_blocks_eligible_lower_businesses']=sum(p[observer+'_both_blocks_eligible_lower'] for p in members)
        for kind in ('dynamic','static'):
            g[kind+'_mean_fee_rates']={k:mean_bounds([p[kind+'_fee_rates'][k] for p in values]) for k in LAYERS}
        groups.append(g)
    if (len(pairs),len(businesses),len(repeats),len(groups))!=(224,112,128,28): raise ValueError('完整配对分母错误')
    return pairs,businesses,repeats,groups


def summarize(output,cases,table):
    from analyze_report_observer import write
    signatures,workloads,decisions={},{},{}
    for c in cases:
        d=output/c['name']; packets=load(d/'packets.json'); ids={p['packet_id']:i for i,p in enumerate(packets)}
        key=c['scenario'],c['seed']; business=[business_packet(p) for p in packets],load(d/'tasks.json')
        if key in workloads: same(business,workloads[key],'完整同业务提供与任务')
        workloads[key]=business
        signatures[c['name']]=sorted([ids[r['packet_id']],x['switch_id']] for r in rows(d/'collector.jsonl')
            if r.get('ether_type')==0x88b6 for x in r['records'])
        decisions[c['name']]=[dict(offset=r['scheduled_offset_us'],skipped=r['skipped'],
            configuration=r.get('selected',{}).get('config'),writes=[{k:w.get(k) for k in ('node','flow','field','value','success')} for w in r.get('writes',[])])
            for r in rows(d/'decisions.jsonl')]
    seeds=load(output/'comparison-plan.json')['fresh_business_seeds']
    pairs,businesses,repeats,groups=records(table,signatures,decisions,seeds)
    for name,value in (('sample-signatures.json',signatures),('decision-signatures.json',decisions),
        ('paired-cases.json',pairs),('business-pairs.json',businesses),('deployment-repeats.json',repeats),('paired-groups.json',groups)):
        write(output/name,value)
    return dict(paired_cases=224,business_pair_groups=112,deployment_repeat_groups=128,scenario_method_groups=28,
        both_within_pairs=sum(p['both_within'] for p in pairs),same_sample_position_pairs=sum(p['same_sample_positions'] for p in pairs),
        same_sampling_repeat_groups=sum(r['same_sampling_positions'] for r in repeats),
        same_decision_repeat_groups=sum(r['same_decision_sequence'] for r in repeats),
        controller_all_businesses_both_blocks_eligible_lower_groups=[{k:g[k] for k in ('scenario','budget_level','method')}
            for g in groups if g['controller_both_blocks_eligible_lower_businesses']==4],
        collector_all_businesses_both_blocks_eligible_lower_groups=[{k:g[k] for k in ('scenario','budget_level','method')}
            for g in groups if g['collector_both_blocks_eligible_lower_businesses']==4],
        max_controller_within_business_midpoint_range=max(r['controller_midpoint_range'] for r in repeats),
        max_collector_within_business_midpoint_range=max(r['collector_midpoint_range'] for r in repeats))
