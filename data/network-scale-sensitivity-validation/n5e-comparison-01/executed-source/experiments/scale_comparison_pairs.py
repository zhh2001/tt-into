"""规模矩阵完整配对，分列独立业务、部署重复、共享年龄及费用。"""
from collections import Counter
from check_observation_wire import load, rows
from check_rolling_reference import same
from diagnose_observer_training import business_packet

KEYS = ('network_id','scenario','business_seed','block','method')


def mean_bounds(values):
    return [None if any(v[i] is None for v in values) else sum(v[i] for v in values)/len(values) for i in (0,1)]


def complete(summary, plan):
    expected = Counter(tuple(e[k] for k in KEYS) for e in plan['execution_order'])
    actual = Counter(tuple(r[k] for k in KEYS) for r in summary)
    same(actual, expected, '完整规模方法/业务/两部署身份')
    if set(actual.values()) != {1}:
        raise ValueError('规模身份重复')


def records(summary, signatures, decisions, plan):
    complete(summary, plan)
    indexed = {tuple(r[k] for k in KEYS):r for r in summary}
    pairs, businesses, repeats, groups = [], [], [], []
    for key, dynamic in sorted(indexed.items()):
        if dynamic['method'] == 'static':
            continue
        static = indexed[key[:-1]+('static',)]
        a, b = dynamic['batch_id']+'/'+dynamic['case'], static['batch_id']+'/'+static['case']
        pair = {k:dynamic[k] for k in KEYS}
        pair.update(dynamic_case=a, static_case=b, dynamic_feasibility=dynamic['window_feasibility'],
            static_feasibility=static['window_feasibility'],
            both_within=dynamic['window_feasibility']==static['window_feasibility']=='within',
            same_sample_positions=signatures[a]==signatures[b],
            dynamic_fee_rates=dynamic['fee_rates'],static_fee_rates=static['fee_rates'],
            dynamic_coverage=dynamic['weighted_active_interval_coverage'],
            static_coverage=static['weighted_active_interval_coverage'])
        for observer in ('controller','collector'):
            x, z = dynamic[observer+'_loss'], static[observer+'_loss']
            delta = [x[0]-z[1],x[1]-z[0]]
            pair[observer+'_dynamic_bounds'],pair[observer+'_static_bounds'] = x,z
            pair[observer+'_difference_bounds'] = delta
            pair[observer+'_direction'] = 'lower' if delta[1]<0 else 'higher' if delta[0]>0 else 'overlap'
            pair[observer+'_midpoint_relative_reduction'] = (sum(z)-sum(x))/sum(z) if sum(z)>0 else None
        pairs.append(pair)
    for key in sorted({(r['network_id'],r['scenario'],r['business_seed'],r['method']) for r in summary}):
        network,scene,seed,method = key
        members = [indexed[network,scene,seed,block,method] for block in plan['blocks']]
        names = [r['batch_id']+'/'+r['case'] for r in members]
        repeat = dict(zip(('network_id','scenario','business_seed','method'),key))
        repeat.update(cases=names,same_sample_positions=signatures[names[0]]==signatures[names[1]],
            same_decision_sequence=decisions[names[0]]==decisions[names[1]],
            feasibility=[r['window_feasibility'] for r in members])
        for observer in ('controller','collector'):
            bounds = [r[observer+'_loss'] for r in members]
            repeat[observer+'_bounds'] = bounds
            repeat[observer+'_common_overlap'] = max(x[0] for x in bounds)<=min(x[1] for x in bounds)
            repeat[observer+'_midpoint_range'] = abs(sum(bounds[0])-sum(bounds[1]))/2
        repeats.append(repeat)
        if method == 'static':
            continue
        values = [p for p in pairs if (p['network_id'],p['scenario'],p['business_seed'],p['method'])==key]
        same(len(values),2,'同业务两部署配对')
        business = dict(zip(('network_id','scenario','business_seed','method'),key))
        business.update(deployment_pairs=2,both_within_pairs=sum(v['both_within'] for v in values))
        for observer in ('controller','collector'):
            for kind in ('dynamic_bounds','static_bounds','difference_bounds'):
                business[observer+'_mean_'+kind] = mean_bounds([p[observer+'_'+kind] for p in values])
            a,z = business[observer+'_mean_dynamic_bounds'],business[observer+'_mean_static_bounds']
            business[observer+'_midpoint_relative_reduction'] = (sum(z)-sum(a))/sum(z) if sum(z)>0 else None
            business[observer+'_directions'] = [p[observer+'_direction'] for p in values]
        businesses.append(business)
    for key in sorted({(p['network_id'],p['scenario'],p['method']) for p in pairs}):
        values = [p for p in pairs if (p['network_id'],p['scenario'],p['method'])==key]
        business = [b for b in businesses if (b['network_id'],b['scenario'],b['method'])==key]
        same(sorted(b['business_seed'] for b in business),sorted(plan['business_seeds']),'四独立业务分母')
        group = dict(zip(('network_id','scenario','method'),key))
        group.update(business_count=4,deployment_pairs=8,both_within_pairs=sum(p['both_within'] for p in values),
            same_sample_position_pairs=sum(p['same_sample_positions'] for p in values),
            dynamic_mean_coverage=sum(p['dynamic_coverage'] for p in values)/8,
            static_mean_coverage=sum(p['static_coverage'] for p in values)/8)
        for observer in ('controller','collector'):
            for kind in ('dynamic_bounds','static_bounds','difference_bounds'):
                group[observer+'_mean_'+kind] = mean_bounds([b[observer+'_mean_'+kind] for b in business])
            a,z = group[observer+'_mean_dynamic_bounds'],group[observer+'_mean_static_bounds']
            group[observer+'_midpoint_relative_reduction'] = (sum(z)-sum(a))/sum(z) if sum(z)>0 else None
            group[observer+'_business_reductions'] = [b[observer+'_midpoint_relative_reduction'] for b in business]
            group[observer+'_directions'] = dict(Counter(p[observer+'_direction'] for p in values))
        for kind in ('dynamic','static'):
            group[kind+'_mean_fee_rates'] = {k:mean_bounds([p[kind+'_fee_rates'][k] for p in values]) for k in values[0][kind+'_fee_rates']}
        groups.append(group)
    same((len(pairs),len(businesses),len(repeats),len(groups)),(360,180,216,45),'规模全部配对分母')
    return pairs,businesses,repeats,groups


def summarize(output, summary, plan):
    signatures, decisions, workloads = {}, {}, {}
    for row in summary:
        name = row['batch_id']+'/'+row['case']
        d = output / name
        packets = load(d/'packets.json')
        normalized = sorted([business_packet(p) for p in packets],key=lambda p:(p['offset_ns'],p['flow']))
        key = row['network_id'],row['scenario'],row['business_seed']
        common = normalized,load(d/'tasks.json'),load(d/'notice-schedule.json')
        if key in workloads:
            same(common,workloads[key],'同业务全部方法和两部署提供、任务及合法需求')
        workloads[key] = common
        ps = {p['packet_id']:p for p in packets}
        signatures[name] = sorted([ps[r['packet_id']]['flow'],ps[r['packet_id']]['offset_ns'],x['switch_id']]
            for r in rows(d/'collector.jsonl') if r.get('ether_type')==0x88b6 for x in r['records'])
        decisions[name] = [dict(offset=r['scheduled_offset_us'],skipped=r['skipped'],
            config=r.get('selected',{}).get('config'),writes=[{k:w.get(k) for k in ('node','flow','field','value','success')} for w in r.get('writes',[])])
            for r in rows(d/'decisions.jsonl')]
    pairs,business,repeats,groups = records(summary,signatures,decisions,plan)
    return dict(sample_signatures=signatures,decision_signatures=decisions,
        paired_cases=pairs,business_pairs=business,deployment_repeats=repeats,paired_groups=groups)
