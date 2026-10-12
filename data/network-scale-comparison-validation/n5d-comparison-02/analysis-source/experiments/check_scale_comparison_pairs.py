"""独立重建完整业务配对、部署变化及四业务平均结果。"""
from collections import Counter
from check_observation_wire import load, rows
from check_rolling_reference import same, near


def compare(a, b, why):
    if isinstance(a, dict):
        same(set(a),set(b),why+'字段')
        for k in a:compare(a[k],b[k],why+'/'+k)
    elif isinstance(a,list):
        same(len(a),len(b),why+'分母')
        for x,z in zip(a,b):compare(x,z,why)
    elif isinstance(a,float) and b is not None:
        near(a,b,why)
    else:
        same(a,b,why)


def means(values):
    return [None if any(v[i] is None for v in values) else sum(v[i] for v in values)/len(values) for i in (0,1)]


def check(output):
    summary=load(output/'summary.json');plan=load(output/'stage-plan.json')
    keys=('network_id','scenario','business_seed','block','method')
    same(Counter(tuple(r[k] for k in keys) for r in summary),Counter(tuple(e[k] for k in keys) for e in plan['execution_order']),'独立完整432身份')
    indexed={tuple(r[k] for k in keys):r for r in summary};checks=1
    signatures,decisions={},{}
    for r in summary:
        name=r['batch_id']+'/'+r['case'];d=output/name
        ps={p['packet_id']:p for p in load(d/'packets.json')}
        signatures[name]=sorted([ps[t['packet_id']]['flow'],ps[t['packet_id']]['offset_ns'],x['switch_id']]
            for t in rows(d/'collector.jsonl') if t.get('ether_type')==0x88b6 for x in t['records'])
        decisions[name]=[dict(offset=v['scheduled_offset_us'],skipped=v['skipped'],config=v.get('selected',{}).get('config'),
            writes=[{k:w.get(k) for k in ('node','flow','field','value','success')} for w in v.get('writes',[])]) for v in rows(d/'decisions.jsonl')]
    same(load(output/'sample-signatures.json'),signatures,'独立实际采样位置');same(load(output/'decision-signatures.json'),decisions,'独立决策与实际写入签名');checks+=2
    pairs=load(output/'paired-cases.json');business=load(output/'business-pairs.json');repeats=load(output/'deployment-repeats.json');groups=load(output/'paired-groups.json')
    same((len(pairs),len(business),len(repeats),len(groups)),(360,180,216,45),'独立完整配对分母');checks+=1
    same(Counter(tuple(p[k] for k in keys) for p in pairs),Counter(k for k in indexed if k[-1]!='static'),'全部动态/静态配对身份');checks+=1
    for p in pairs:
        k=tuple(p[n] for n in keys);a=indexed[k];z=indexed[k[:-1]+('static',)]
        an,zn=a['batch_id']+'/'+a['case'],z['batch_id']+'/'+z['case']
        expected={n:a[n] for n in keys}
        expected.update(dynamic_case=an,static_case=zn,dynamic_feasibility=a['window_feasibility'],static_feasibility=z['window_feasibility'],
            both_within=a['window_feasibility']==z['window_feasibility']=='within',same_sample_positions=signatures[an]==signatures[zn],
            dynamic_fee_rates=a['fee_rates'],static_fee_rates=z['fee_rates'],dynamic_coverage=a['weighted_active_interval_coverage'],static_coverage=z['weighted_active_interval_coverage'])
        for observer in ('controller','collector'):
            x,y=a[observer+'_loss'],z[observer+'_loss'];delta=[x[0]-y[1],x[1]-y[0]]
            expected.update({observer+'_dynamic_bounds':x,observer+'_static_bounds':y,observer+'_difference_bounds':delta,
                observer+'_direction':'lower' if delta[1]<0 else 'higher' if delta[0]>0 else 'overlap',
                observer+'_midpoint_relative_reduction':(sum(y)-sum(x))/sum(y) if sum(y)>0 else None})
        compare(p,expected,'独立逐配对');checks+=len(expected)
    bkeys=('network_id','scenario','business_seed','method')
    same(Counter(tuple(b[k] for k in bkeys) for b in business),Counter({tuple(k[i] for i in (0,1,2,4)):1 for k in indexed if k[-1]!='static'}),'独立180业务组')
    same(Counter(tuple(b[k] for k in bkeys) for b in repeats),Counter({tuple(k[i] for i in (0,1,2,4)):1 for k in indexed}),'独立216重复组');checks+=2
    for b in business:
        values=[p for p in pairs if all(p[k]==b[k] for k in bkeys)]
        same(len(values),2,'独立两块业务平均');same(b['deployment_pairs'],2,'业务部署分母')
        same(b['both_within_pairs'],sum(p['both_within'] for p in values),'业务费用资格');checks+=3
        for observer in ('controller','collector'):
            for kind in ('dynamic_bounds','static_bounds','difference_bounds'):
                compare(b[observer+'_mean_'+kind],means([p[observer+'_'+kind] for p in values]),'独立业务条件均值');checks+=2
            a,z=b[observer+'_mean_dynamic_bounds'],b[observer+'_mean_static_bounds']
            compare(b[observer+'_midpoint_relative_reduction'],(sum(z)-sum(a))/sum(z) if sum(z)>0 else None,'独立业务相对变化')
            same(b[observer+'_directions'],[p[observer+'_direction'] for p in values],'独立业务两块方向');checks+=2
    for r in repeats:
        members=[indexed[r['network_id'],r['scenario'],r['business_seed'],b,r['method']] for b in (0,1)]
        names=[m['batch_id']+'/'+m['case'] for m in members]
        same(r['cases'],names,'独立重复案例关联');same(r['same_sample_positions'],signatures[names[0]]==signatures[names[1]],'独立采样重复')
        same(r['same_decision_sequence'],decisions[names[0]]==decisions[names[1]],'独立决策重复');same(r['feasibility'],[m['window_feasibility'] for m in members],'独立重复费用');checks+=4
        for observer in ('controller','collector'):
            v=[m[observer+'_loss'] for m in members]
            compare(r[observer+'_bounds'],v,'独立重复年龄');same(r[observer+'_common_overlap'],max(x[0] for x in v)<=min(x[1] for x in v),'独立条件交集')
            compare(r[observer+'_midpoint_range'],abs(sum(v[0])-sum(v[1]))/2,'独立重复变异');checks+=3
    gkeys=('network_id','scenario','method')
    same(Counter(tuple(g[k] for k in gkeys) for g in groups),Counter({tuple(p[k] for k in gkeys):1 for p in pairs}),'独立45场景方法组');checks+=1
    for g in groups:
        values=[p for p in pairs if all(p[k]==g[k] for k in gkeys)];bs=[b for b in business if all(b[k]==g[k] for k in gkeys)]
        same(sorted(b['business_seed'] for b in bs),sorted(plan['business_seeds']),'独立四业务来源');same((g['business_count'],g['deployment_pairs']),(4,8),'组分母')
        same(g['both_within_pairs'],sum(p['both_within'] for p in values),'组预算资格');same(g['same_sample_position_pairs'],sum(p['same_sample_positions'] for p in values),'组采样位置');checks+=4
        for kind in ('dynamic','static'):
            compare(g[kind+'_mean_coverage'],sum(p[kind+'_coverage'] for p in values)/8,'独立共享覆盖均值')
            compare(g[kind+'_mean_fee_rates'],{k:means([p[kind+'_fee_rates'][k] for p in values]) for k in values[0][kind+'_fee_rates']},'独立全部费用均值');checks+=2
        for observer in ('controller','collector'):
            for kind in ('dynamic_bounds','static_bounds','difference_bounds'):
                compare(g[observer+'_mean_'+kind],means([p[observer+'_'+kind] for p in values]),'独立八部署条件均值');checks+=2
            a,z=g[observer+'_mean_dynamic_bounds'],g[observer+'_mean_static_bounds']
            compare(g[observer+'_midpoint_relative_reduction'],(sum(z)-sum(a))/sum(z) if sum(z)>0 else None,'独立组相对变化')
            compare(g[observer+'_business_reductions'],[b[observer+'_midpoint_relative_reduction'] for b in bs],'四业务效应完整')
            same(g[observer+'_directions'],dict(Counter(p[observer+'_direction'] for p in values)),'独立组方向');checks+=3
    return checks
