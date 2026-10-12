"""独立验收独立业务比较的批次、两位置积分、完整配对与重复部署。"""
import argparse
import csv
import json
from pathlib import Path

from check_literature_comparison_matrix import audit
from literature_comparison_identity import summary_identity, complete
from literature_comparison_analysis_contract import validate_contract
from check_matched_network import load, rows, sha, charges_for
from check_rolling_reference import same, near
from network_entry_bounds import costs, output_frames
from observation_diagnostics import task_integral


def check_integrals(tasks, samples, origin, saved):
    denominator = sum(t['weight'] * (t['end_us'] - t['start_us']) for t in tasks)
    count = 0
    for side, gen, at in (('lower', 'generation_upper_ns', 'entered_lower_ns'),
                          ('upper', 'generation_lower_ns', 'entered_upper_ns')):
        total = 0
        for task in tasks:
            value, _ = task_integral(task, samples, origin, gen, at)
            total += task['weight'] * (task['end_us'] - task['start_us']) * value
            count += 1
        near(total / denominator, saved['loss_' + side], '独立纳秒任务积分与全部任务分母')
    near(denominator, saved['weighted_duration_us'], '完整活跃加权时长')
    same(len(samples), saved['sample_count'], '完整缓存推进样本')
    return count + 4


def check(output):
    m = load(output / 'manifest.json')
    contract = validate_contract(output / 'analysis-contract.json', output / 'stage-plan.json')
    same(m['analysis_source_sha256'], contract['analysis_source_sha256'], '首例前分析版本绑定')
    verified = audit(output)
    rpc = rows(output / 'rpc-operations.jsonl'); frames = output_frames(output, m['cases'])
    with (output / 'summary.csv').open() as stream: table = list(csv.DictReader(stream))
    with (output / 'report-availability.csv').open() as stream: available = list(csv.DictReader(stream))
    same([r['case'] for r in table], [c['name'] for c in m['cases']], '完整汇总顺序')
    count, task_count, samples_total = 0, 0, 0
    for name, digest in m['analysis_source_sha256'].items():
        same(sha(output / 'executed-analysis-source' / name), digest, '实际分析源码快照'); count += 1
    for case, summary in zip(m['cases'], table):
        count += summary_identity(summary, case)
        directory = output / case['name']; origin = case['origin_monotonic_ns']
        cost = costs(directory, case, frames[case['name']], charges_for(directory, rpc))
        same(cost, load(directory / 'window-costs.json'), '独立实际分层窗口费用')
        same(summary['window_feasibility'], cost['window_feasibility'], '全部超限状态保留')
        near(float(summary['api_bytes_per_s']), cost['api_bytes_per_s'], 'API实际费用汇总')
        near(float(summary['local_notice_bytes_per_s']), cost['local_notice_bytes_per_s'], '通知实际费用汇总')
        for resource in ('e1', 'e2', 'e3', 'export'):
            for side in ('lower', 'upper'):
                value = cost[f'measurement_{side}_bytes']
                if value is None: same(summary[f'{resource}_{side}_bytes_per_s'], '', '未知费用保留')
                else: near(float(summary[f'{resource}_{side}_bytes_per_s']), value[resource] / 8, '实际帧费用条件界')
                count += 1
        generation, samples = load(directory / 'generation-bounds.json'), load(directory / 'controller-samples.json')
        raw = [r for r in rows(directory / 'collector.jsonl') if r.get('ether_type') == 0x88b6]
        sends = {r['packet_id']: r for r in rows(directory / 'send.jsonl')}
        business = {r['packet_id']: r for r in rows(directory / 'destination.jsonl') if r.get('src') == '10.0.0.1'}
        interval = {(r['packet_id'], r['node']): r for r in generation['intervals']}
        for node in (1, 2, 3):
            relevant = [(sends[r['packet_id']]['send_before_monotonic_ns'] - x['generated_us'] * 1000 - 1000,
                         min(business[r['packet_id']]['receive_monotonic_ns'], r['receive_monotonic_ns']) - x['generated_us'] * 1000)
                        for r in raw for x in r['records'] if x['switch_id'] == node]
            if relevant:
                clock = generation['local_clock_offset'][str(node)]
                same(clock['lower_ns'], max(a for a, _ in relevant), '独立本地时间偏移下界')
                same(clock['upper_ns'], min(b for _, b in relevant), '独立本地时间偏移上界')
                same(clock['compatible'], clock['lower_ns'] <= clock['upper_ns'], '条件偏移相容性')
                count += 3
        for r in raw:
            for record in r['records']:
                s = interval[r['packet_id'], record['switch_id']]
                lo, hi = sends[r['packet_id']]['send_before_monotonic_ns'], min(business[r['packet_id']]['receive_monotonic_ns'], r['receive_monotonic_ns'])
                clock = generation['local_clock_offset'][str(record['switch_id'])]
                if clock['compatible']:
                    lo = max(lo, record['generated_us'] * 1000 + clock['lower_ns'])
                    hi = min(hi, record['generated_us'] * 1000 + clock['upper_ns'] + 1000)
                same([s['generation_lower_ns'], s['generation_upper_ns']], [lo, hi], '实际生成因果条件界')
                count += 1
        expected, known = [], {}
        reports_visible = 0
        for observed in rows(directory / 'report-observations.jsonl'):
            if observed['skipped']: continue
            event = observed['availability']
            for report in observed['fresh_reports']:
                physical = [r for r in raw if (r['session'], r['flow_tag']) == (report['flow']['session'], report['flow']['tag'])
                            and (r['receive_monotonic_ns'] - origin) // 1000 == report['received_us']]
                if len(physical) != 1: raise ValueError('验证来源不唯一')
                r = physical[0]; reports_visible += 1
                saved = [a for a in available if a['case'] == case['name'] and int(a['packet_id']) == r['packet_id']]
                same(len(saved), 1, '每条首次取得报告只汇总一次')
                a = saved[0]
                for key, value in (('observation_index', observed['observation_index']), ('received_ns', r['receive_monotonic_ns']),
                                   ('entered_lower_ns', event['ingest_started_ns']), ('entered_upper_ns', event['available_ns'])):
                    same(int(a[key]), value, '真实首次进入观察事件'); count += 1
                near(float(a['receipt_to_entry_upper_ms']), (event['available_ns'] - r['receive_monotonic_ns']) / 1e6, '实际观察等待上界')
                for x in report['records']:
                    node, generated = x['node'], x['generated_local_us']
                    if node in known and not 0 < (generated - known[node]) % 2**48 < 2**47: continue
                    known[node] = generated
                    i = interval[r['packet_id'], node]
                    expected.append({'node': node, 'packet_id': r['packet_id'], 'generated_local_us': generated,
                        'observation_index': observed['observation_index'], 'received_ns': r['receive_monotonic_ns'],
                        'generation_lower_ns': i['generation_lower_ns'], 'generation_upper_ns': i['generation_upper_ns'],
                        'entered_lower_ns': event['ingest_started_ns'], 'entered_upper_ns': event['available_ns']})
                count += 2
        same(samples, expected, '全部实际共享缓存推进来源与首次可用区间')
        tasks = load(directory / 'tasks.json'); visible = load(directory / 'controller-visible-bounds.json')
        count += check_integrals(tasks, samples, origin, visible)
        collector = [dict(r, entered_lower_ns=r['received_ns'], entered_upper_ns=r['received_ns']) for r in generation['intervals']]
        denominator = sum(t['weight'] * (t['end_us'] - t['start_us']) for t in tasks)
        count += check_integrals(tasks, collector, origin, dict(generation, weighted_duration_us=denominator, sample_count=len(collector)))
        for observer, bounds in (('collector', generation), ('controller', visible)):
            for side in ('lower', 'upper'):
                near(float(summary[observer + '_loss_' + side]), bounds['loss_' + side], '双位置年龄条件界汇总'); count += 1
        own = rpc[case['rpc_start_index']:case['rpc_stop_index']]
        same(int(summary['paid_counter_calls']), sum(r['kind'] == 'online_observation' for r in own), '实际源计数频率')
        same(int(summary['visible_reports']), reports_visible, '合法实收报告汇总')
        same(int(summary['shared_cache_advances']), len(samples), '共享任务不重复计收益')
        same(int(summary['unread_tail_reports']), len(raw) - reports_visible, '末段不补更新')
        task_count += len(tasks); samples_total += len(samples); count += 8
    findings = load(output / 'findings.json')
    for field, source in (('packets', 'packet_count'), ('reports', 'report_count'), ('decisions', 'decision_count'),
                          ('completed_observations', 'completed_observations'), ('successful_updates', 'successful_updates')):
        same(findings[field], verified[source], '完整试点计数'); count += 1
    same(findings['window_feasibility'], dict(__import__('collections').Counter(r['window_feasibility'] for r in table)), '全部费用失败分母')
    same(findings['visible_reports'], len(available), '完整可用报告分母')
    same(findings['shared_cache_advances'], samples_total, '完整共享缓存分母')
    pair_checks = check_pairs(output, m['cases'], table)
    diagnostic_checks = check_diagnostics(output, m['cases'], table)
    return {'status': 'passed', 'cases': len(table), 'tasks': task_count, 'controller_samples': samples_total,
            'pair_checks': pair_checks,
            'analysis_checks': count + 3 + diagnostic_checks, 'network_checks': sum(verified[k] for k in ('checks', 'live_control_checks', 'observer_checks', 'cache_and_budget_checks')),
            'analysis_manifest_sha256': sha(output / 'manifest.json')}


def typed_table(table):
    ints={'seed','block','preset_index','parameter_index','position','packets','reports','completed_observations','skipped_observations',
          'decisions','skipped_decisions','paid_counter_calls','successful_updates','visible_reports','shared_cache_advances',
          'unread_tail_reports','controller_rss_start_bytes','controller_rss_end_bytes'}
    flags={'wall_clock_compatible','generation_clock_compatible','initial_notice_first_cycle'}
    strings={'case','scenario','budget_level','method','window_feasibility'}
    return [{k:(None if v=='' else int(v) if k in ints else v=='True' if k in flags else v if k in strings else float(v))
             for k,v in r.items()} for r in table]



def check_pairs(output,cases,table):
    from collections import Counter
    from diagnose_observer_training import business_packet
    plan=load(output/'stage-plan.json'); seeds=plan['business_seeds']
    values=typed_table(table); complete(values, seeds)
    byname={r['case']:r for r in values}
    identities={tuple(c[k] for k in ('scenario','budget_level','seed','block','method')):c for c in cases}
    same((len(values),len(byname),len(identities)),(320,320,320),'完整身份与全部案例')
    signatures,workloads,decision_signatures={},{},{}; count=1
    for c in cases:
        d=output/c['name']; row=byname[c['name']]
        for key in ('scenario','budget_level','seed','block','method','preset_index','parameter_index','position'):
            same(row[key],c[key],'汇总实际身份'); count+=1
        packets=load(d/'packets.json'); ids={p['packet_id']:i for i,p in enumerate(packets)}
        same(len(ids),len(packets),'验证身份唯一')
        business=[business_packet(p) for p in packets],load(d/'tasks.json')
        key=c['scenario'],c['seed']
        if key in workloads: same(business,workloads[key],'全方法及两部署的完整同业务')
        workloads[key]=business
        signatures[c['name']]=sorted([ids[r['packet_id']],x['switch_id']] for r in rows(d/'collector.jsonl')
            if r.get('ether_type')==0x88b6 for x in r['records'])
        decision_signatures[c['name']]=[dict(offset=r['scheduled_offset_us'],skipped=r['skipped'],
            configuration=r.get('selected',{}).get('config'),writes=[{k:w.get(k) for k in ('node','flow','field','value','success')} for w in r.get('writes',[])])
            for r in rows(d/'decisions.jsonl')]
    same(load(output/'sample-signatures.json'),signatures,'实际采样位置全部来源')
    same(load(output/'decision-signatures.json'),decision_signatures,'真实决策序列来源')
    pairs,businesses,repeats,groups=[load(output/name) for name in
        ('paired-cases.json','business-pairs.json','deployment-repeats.json','paired-groups.json')]
    same(tuple(map(len,(pairs,businesses,repeats,groups))),(288,144,160,36),'全部配对及重复分母'); count+=3
    pairkeys=set()
    layers=('e1','e2','e3','export','api_serialized','local_notice_payload')
    for p in pairs:
        key=tuple(p[k] for k in ('scenario','budget_level','seed','block','method'))
        if key in pairkeys or p['method']=='static': raise ValueError('重复或非法动态配对')
        pairkeys.add(key); dynamic=identities[key]; static=identities[key[:-1]+('static',)]
        d,b=byname[dynamic['name']],byname[static['name']]
        same((p['dynamic_case'],p['static_case']),(dynamic['name'],static['name']),'相同业务与块静态参照')
        same(p['parameter_index'],dynamic['parameter_index'],'冻结参数不选优')
        same(p['preset_index'],dynamic['preset_index'],'文献全局预设绑定')
        same(p['dynamic_feasibility'],d['window_feasibility'],'动态全部费用状态')
        same(p['static_feasibility'],b['window_feasibility'],'静态全部费用状态')
        same(p['both_within'],d['window_feasibility']==b['window_feasibility']=='within','双方预算资格')
        same(p['same_sample_positions'],signatures[d['case']]==signatures[b['case']],'实际采样位置匹配')
        same(p['position_symmetric_difference'],len(set(map(tuple,signatures[d['case']]))^set(map(tuple,signatures[b['case']]))),'实际位置差异')
        for observer in ('controller','collector'):
            a=[d[observer+'_loss_'+s] for s in ('lower','upper')];z=[b[observer+'_loss_'+s] for s in ('lower','upper')]
            delta=[a[0]-z[1],a[1]-z[0]]
            same(p[observer+'_dynamic_bounds'],a,'动态年龄来源'); same(p[observer+'_static_bounds'],z,'静态年龄来源')
            same(p[observer+'_difference_bounds'],delta,'条件配对差值')
            same(p[observer+'_direction'],'lower' if delta[1]<0 else 'higher' if delta[0]>0 else 'overlap','两观察位置方向')
            same(p[observer+'_midpoint_relative_reduction'],(sum(z)-sum(a))/sum(z) if sum(z)>0 else None,'配对相对变化')
        for kind,r in (('dynamic',d),('static',b)):
            actual={k:[r[k+'_'+s+'_bytes_per_s'] for s in ('lower','upper')] for k in layers[:4]}
            actual.update(api_serialized=[r['api_bytes_per_s']]*2,local_notice_payload=[r['local_notice_bytes_per_s']]*2)
            same(p[kind+'_fee_rates'],actual,'六层真实费用，未知不填零')
        count+=19
    businesskeys=set()
    for p in businesses:
        key=tuple(p[k] for k in ('scenario','budget_level','seed','method'))
        if key in businesskeys: raise ValueError('重复独立业务组')
        businesskeys.add(key)
        members=[r for r in pairs if all(r[k]==p[k] for k in ('scenario','budget_level','seed','method'))]
        same(sorted(r['block'] for r in members),[1,2],'每业务完整两块')
        same(p['deployment_pairs'],2,'业务两部署分母')
        same(p['both_within_pairs'],sum(r['both_within'] for r in members),'业务内预算资格')
        same(p['dynamic_cases'],[r['dynamic_case'] for r in members],'业务动态身份')
        same(p['static_cases'],[r['static_case'] for r in members],'业务静态身份')
        for observer in ('controller','collector'):
            for kind in ('dynamic_bounds','static_bounds','difference_bounds'):
                same(p[observer+'_mean_'+kind],[sum(r[observer+'_'+kind][i] for r in members)/2 for i in (0,1)],'两部署业务均值')
            a,z=p[observer+'_mean_dynamic_bounds'],p[observer+'_mean_static_bounds']
            same(p[observer+'_midpoint_relative_reduction'],(sum(z)-sum(a))/sum(z) if sum(z)>0 else None,'业务相对变化')
            same(p[observer+'_directions'],dict(Counter(r[observer+'_direction'] for r in members)),'完整业务方向')
            same(p[observer+'_both_blocks_eligible_lower'],all(r['both_within'] and r[observer+'_direction']=='lower' for r in members),'业务两块方向和资格')
        count+=17
    repeatkeys=set()
    for p in repeats:
        key=tuple(p[k] for k in ('scenario','budget_level','seed','method'))
        if key in repeatkeys: raise ValueError('重复部署组')
        repeatkeys.add(key)
        names=[identities[key[:3]+(block,key[3])]['name'] for block in (1,2)]
        same(p['cases'],names,'实际两块身份')
        same(p['same_sampling_positions'],signatures[names[0]]==signatures[names[1]],'重复采样位置')
        same(p['position_symmetric_difference'],len(set(map(tuple,signatures[names[0]]))^set(map(tuple,signatures[names[1]]))),'重复采样差异')
        same(p['same_decision_sequence'],decision_signatures[names[0]]==decision_signatures[names[1]],'重复实际决策变化')
        same(p['window_statuses'],[byname[n]['window_feasibility'] for n in names],'重复全部费用状态')
        for observer in ('controller','collector'):
            bs=[[byname[n][observer+'_loss_'+s] for s in ('lower','upper')] for n in names]
            same(p[observer+'_loss_bounds'],bs,'重复两位置年龄来源')
            same(p[observer+'_intervals_overlap'],max(v[0] for v in bs)<=min(v[1] for v in bs),'条件界交叠')
            near(p[observer+'_midpoint_range'],abs(sum(bs[0])-sum(bs[1]))/2,'两部署中点变异')
        count+=11
    groupkeys=set()
    for g in groups:
        key=tuple(g[k] for k in ('scenario','budget_level','method'))
        if key in groupkeys: raise ValueError('重复场景方法组')
        groupkeys.add(key)
        members=[r for r in businesses if all(r[k]==g[k] for k in ('scenario','budget_level','method'))]
        ps=[r for r in pairs if all(r[k]==g[k] for k in ('scenario','budget_level','method'))]
        same(sorted(r['seed'] for r in members),sorted(seeds),'四条独立业务')
        same((g['business_count'],g['deployment_pairs']),(4,8),'部署不增加独立分母')
        same(g['both_within_pairs'],sum(r['both_within'] for r in ps),'完整组预算资格')
        same(g['same_sample_position_pairs'],sum(r['same_sample_positions'] for r in ps),'完整组采样位置')
        for observer in ('controller','collector'):
            for kind in ('dynamic_bounds','static_bounds','difference_bounds'):
                same(g[observer+'_mean_'+kind],[sum(r[observer+'_mean_'+kind][i] for r in members)/4 for i in (0,1)],'四业务组均值')
            a,z=g[observer+'_mean_dynamic_bounds'],g[observer+'_mean_static_bounds']
            same(g[observer+'_midpoint_relative_reduction'],(sum(z)-sum(a))/sum(z) if sum(z)>0 else None,'组相对变化')
            same(g[observer+'_directions'],dict(Counter(r[observer+'_direction'] for r in ps)),'完整组方向')
            same(g[observer+'_both_blocks_eligible_lower_businesses'],sum(r[observer+'_both_blocks_eligible_lower'] for r in members),'改善独立业务分母')
        for kind in ('dynamic','static'):
            expected={k:[sum(r[kind+'_fee_rates'][k][i] for r in ps)/8 if all(r[kind+'_fee_rates'][k][i] is not None for r in ps) else None for i in (0,1)] for k in layers}
            same(g[kind+'_mean_fee_rates'],expected,'组费用未知不被均值掩盖')
        count+=18
    return count


def check_diagnostics(output,cases,table):
    from collections import Counter
    values=typed_table(table); findings=load(output/'findings.json')
    complete(values, load(output/'stage-plan.json')['business_seeds'])
    with (output/'observation-batches.csv').open() as stream: batches=list(csv.DictReader(stream))
    count=0
    rpc=rows(output/'rpc-operations.jsonl')
    for c,r in zip(cases,values):
        d=output/c['name']; observations=rows(d/'report-observations.jsonl')
        bs=[b for b in batches if b['case']==c['name']]
        completed=[o for o in observations if not o['skipped']]
        same(len(bs),len(completed),'全部真实观察批次汇总')
        for b,o in zip(bs,completed):
            e=o['availability']
            same(int(b['observation_index']),o['observation_index'],'实际观察批次身份')
            near(float(b['read_ms']),(e['read_finished_ns']-e['read_started_ns'])/1e6,'批次读取真实耗时')
            near(float(b['ingest_ms']),(e['available_ns']-e['ingest_started_ns'])/1e6,'批次更新真实耗时')
            near(float(b['lateness_ms']),o['lateness_ns']/1e6,'实际观察迟到')
            same(int(b['input_rows']),e['row_count'],'实际完整前缀行数')
            same(int(b['new_reports']),len(o['fresh_reports']),'批次实际新报告');count+=6
        ds=rows(d/'decisions.jsonl')
        own=[x for x in rpc[c['rpc_start_index']:c['rpc_stop_index']] if x['kind'] in ('update','online_observation')]
        solver=[(x['solver_finished_ns']-x['solver_started_ns'])/1e6 for x in ds if 'solver_started_ns' in x]
        expected=dict(completed_observations=len(completed),skipped_observations=len(observations)-len(completed),
            decisions=len(ds),skipped_decisions=sum(x.get('skipped',False) for x in ds),
            successful_updates=c['successful_updates'],packets=c['packet_count'],reports=c['report_count'],
            observation_max_lateness_ms=max(x['lateness_ns']/1e6 for x in observations),
            read_max_ms=max(float(x['read_ms']) for x in bs),ingest_max_ms=max(float(x['ingest_ms']) for x in bs),
            solver_max_ms=max(solver,default=None),
            rpc_max_ms=max(((x['after_monotonic_ns']-x['before_monotonic_ns'])/1e6 for x in own),default=None),
            sender_max_lateness_ms=max(x['schedule_lateness_ns']/1e6 for x in rows(d/'send.jsonl')))
        notices=rows(d/'notices.jsonl')
        expected['initial_notice_first_cycle']=bool(notices and any(n['version']==notices[0]['message']['version'] for n in ds[0].get('view',{}).get('notifications',[])))
        host=load(d/'host.json')['usage']['controller']
        expected.update(controller_mean_cpu_cores=host['mean_cpu_cores'],controller_rss_start_bytes=host['rss_start_bytes'],controller_rss_end_bytes=host['rss_end_bytes'])
        for k,v in expected.items():same(r[k],v,'实际计数、耗时及宿主资源来源');count+=1
    same(findings['business_seed_count'],4,'四条独立业务')
    same(findings['skipped_observations'],sum(r['skipped_observations'] for r in values),'全部跳过保留')
    same(findings['paid_counter_calls'],sum(r['paid_counter_calls'] for r in values),'付费计数独立汇总')
    with (output/'method-aggregate.csv').open() as stream: aggregates=list(csv.DictReader(stream))
    expected_keys={(r['scenario'],r['budget_level'],r['method']) for r in values}
    same({(r['scenario'],r['budget_level'],r['method']) for r in aggregates},expected_keys,'全部40方法单元')
    same(len(aggregates),40,'方法单元无重复')
    for g in aggregates:
        members=[r for r in values if all(r[k]==g[k] for k in ('scenario','budget_level','method'))]
        same(int(g['case_count']),8,'四业务各两部署')
        same(int(g['independent_business_count']),4,'真实独立业务分母')
        for state in ('within','exceeds','unavailable'):same(int(g[state+'_cases']),sum(r['window_feasibility']==state for r in members),'全部费用状态汇总');count+=1
        for observer in ('controller','collector'):
            for side in ('lower','upper'):near(float(g[observer+'_mean_loss_'+side]),sum(r[observer+'_loss_'+side] for r in members)/8,'方法单元两位置均值');count+=1
        for field in members[0]:
            if not field.endswith('_bytes_per_s'):continue
            if all(r[field] is not None for r in members):near(float(g[field]),sum(r[field] for r in members)/8,'方法单元分层费用')
            else:same(g[field],'','未知费用均值保留')
            count+=1
    pairs,businesses,repeats,groups=[load(output/name) for name in ('paired-cases.json','business-pairs.json','deployment-repeats.json','paired-groups.json')]
    expected=dict(paired_cases=len(pairs),business_pair_groups=len(businesses),deployment_repeat_groups=len(repeats),scenario_method_groups=len(groups),
        both_within_pairs=sum(p['both_within'] for p in pairs),same_sample_position_pairs=sum(p['same_sample_positions'] for p in pairs),
        same_sampling_repeat_groups=sum(r['same_sampling_positions'] for r in repeats),same_decision_repeat_groups=sum(r['same_decision_sequence'] for r in repeats),
        controller_all_businesses_both_blocks_eligible_lower_groups=[{k:g[k] for k in ('scenario','budget_level','method')} for g in groups if g['controller_both_blocks_eligible_lower_businesses']==4],
        collector_all_businesses_both_blocks_eligible_lower_groups=[{k:g[k] for k in ('scenario','budget_level','method')} for g in groups if g['collector_both_blocks_eligible_lower_businesses']==4],
        max_controller_within_business_midpoint_range=max(r['controller_midpoint_range'] for r in repeats),
        max_collector_within_business_midpoint_range=max(r['collector_midpoint_range'] for r in repeats))
    same(findings['paired_diagnostics'],expected,'完整配对和部署变异汇总')
    return count+6


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--analysis', type=Path, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args(); result = check(a.analysis)
    if a.output: a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
