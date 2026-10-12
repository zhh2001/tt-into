"""选中全局参数的64例结果与完整56个同业务静态配对。"""
from collections import Counter
from check_matched_network import load,rows
from check_rolling_reference import same
from diagnose_observer_training import business_packet
from observer_calibration_contract import LAYERS


def selected_rows(table,frozen):
    indices={r['method']:r['selected_parameter'] for r in frozen}
    if len(indices)!=3 or any(i is None for i in indices.values()): raise ValueError('三个全局参数未冻结')
    selected=[r for r in table if r['method'] not in indices or r['parameter_index']==indices[r['method']]]
    if len(selected)!=64: raise ValueError('选中八方法完整64例分母不符')
    return selected


def paired_records(selected,signatures):
    pairs=[]
    for dynamic in selected:
        if dynamic['method']=='static': continue
        static=next(r for r in selected if r['method']=='static' and
            (r['scenario'],r['budget_level'],r['seed'])==(dynamic['scenario'],dynamic['budget_level'],dynamic['seed']))
        record=dict(scenario=dynamic['scenario'],budget_level=dynamic['budget_level'],seed=dynamic['seed'],
            method=dynamic['method'],parameter_index=dynamic['parameter_index'],dynamic_case=dynamic['case'],static_case=static['case'],
            both_within=all(r['window_feasibility']=='within' for r in (dynamic,static)),
            same_sample_positions=signatures[dynamic['case']]==signatures[static['case']])
        for observer in ('collector','controller'):
            a=[dynamic[observer+'_loss_'+s] for s in ('lower','upper')]
            b=[static[observer+'_loss_'+s] for s in ('lower','upper')]
            delta=[a[0]-b[1],a[1]-b[0]]
            record[observer+'_dynamic_bounds']=a; record[observer+'_static_bounds']=b
            record[observer+'_difference_bounds']=delta
            record[observer+'_direction']='lower' if delta[1]<0 else 'higher' if delta[0]>0 else 'overlap'
        for kind,row in (('dynamic',dynamic),('static',static)):
            record[kind+'_fee_rates']={k:([row[k+'_lower_bytes_per_s'],row[k+'_upper_bytes_per_s']] if k in LAYERS[:4] else
                [row['api_bytes_per_s']]*2 if k=='api_serialized' else [row['local_notice_bytes_per_s']]*2) for k in LAYERS}
        pairs.append(record)
    groups=[]
    for scenario,level,method in sorted({(r['scenario'],r['budget_level'],r['method']) for r in pairs}):
        values=[r for r in pairs if (r['scenario'],r['budget_level'],r['method'])==(scenario,level,method)]
        if sorted(r['seed'] for r in values)!=[1301,1423]: raise ValueError('完整双校准业务配对缺失')
        group=dict(scenario=scenario,budget_level=level,method=method,case_count=2,
            both_within_pairs=sum(r['both_within'] for r in values),same_sample_position_pairs=sum(r['same_sample_positions'] for r in values))
        for observer in ('collector','controller'):
            group[observer+'_both_eligible_lower']=all(r['both_within'] and r[observer+'_direction']=='lower' for r in values)
            group[observer+'_directions']=dict(Counter(r[observer+'_direction'] for r in values))
            for key in ('dynamic_bounds','static_bounds','difference_bounds'):
                group[observer+'_mean_'+key]=[sum(r[observer+'_'+key][i] for r in values)/2 for i in (0,1)]
        for kind in ('dynamic','static'):
            group[kind+'_mean_fee_rates']={k:[sum(r[kind+'_fee_rates'][k][i] for r in values)/2 for i in (0,1)] for k in LAYERS}
        groups.append(group)
    return pairs,groups


def summarize(output,cases,table,frozen):
    from analyze_report_observer import write
    signatures,workloads={},{}
    for c in cases:
        d=output/c['name']; packets=load(d/'packets.json'); ids={p['packet_id']:i for i,p in enumerate(packets)}
        key=c['scenario'],c['seed']
        business=[business_packet(p) for p in packets],load(d/'tasks.json')
        if key in workloads: same(business,workloads[key],'同业务提供与任务')
        workloads[key]=business
        signatures[c['name']]=sorted([ids[r['packet_id']],x['switch_id']] for r in rows(d/'collector.jsonl') if r.get('ether_type')==0x88b6 for x in r['records'])
    selected=selected_rows(table,frozen); pairs,groups=paired_records(selected,signatures)
    write(output/'sample-signatures.json',signatures);write(output/'selected-cases.json',selected)
    write(output/'paired-cases.json',pairs);write(output/'paired-groups.json',groups)
    return dict(selected_cases=len(selected),pairs=len(pairs),groups=len(groups),
        both_within_pairs=sum(r['both_within'] for r in pairs),same_sample_position_pairs=sum(r['same_sample_positions'] for r in pairs),
        eligible_controller_lower_groups=[{k:r[k] for k in ('scenario','budget_level','method')} for r in groups if r['controller_both_eligible_lower']],
        eligible_collector_lower_groups=[{k:r[k] for k in ('scenario','budget_level','method')} for r in groups if r['collector_both_eligible_lower']])
