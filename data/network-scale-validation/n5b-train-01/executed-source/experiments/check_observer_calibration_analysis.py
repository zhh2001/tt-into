"""独立验收动态全局校准的批次、两位置积分、完整参数排序与静态配对。"""
import argparse
import csv
import json
from pathlib import Path

from check_observer_calibration import audit
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
    verified = audit(output)
    rpc = rows(output / 'rpc-operations.jsonl'); frames = output_frames(output, m['cases'])
    with (output / 'summary.csv').open() as stream: table = list(csv.DictReader(stream))
    with (output / 'report-availability.csv').open() as stream: available = list(csv.DictReader(stream))
    same([r['case'] for r in table], [c['name'] for c in m['cases']], '完整汇总顺序')
    count, task_count, samples_total = 0, 0, 0
    for name, digest in m['analysis_source_sha256'].items():
        same(sha(output / 'executed-analysis-source' / name), digest, '实际分析源码快照'); count += 1
    for case, summary in zip(m['cases'], table):
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
    selection_checks = check_selection(output, table)
    pair_checks = check_pairs(output, m['cases'], table)
    return {'status': 'passed', 'cases': len(table), 'tasks': task_count, 'controller_samples': samples_total,
            'selection_checks': selection_checks, 'pair_checks': pair_checks,
            'analysis_checks': count + 3, 'network_checks': sum(verified[k] for k in ('checks', 'live_control_checks', 'observer_checks', 'cache_and_budget_checks')),
            'analysis_manifest_sha256': sha(output / 'manifest.json')}


def typed_table(table):
    ints={'seed','parameter_index','position','packets','reports','completed_observations','skipped_observations',
          'decisions','skipped_decisions','paid_counter_calls','successful_updates','visible_reports','shared_cache_advances',
          'unread_tail_reports','controller_rss_start_bytes','controller_rss_end_bytes'}
    flags={'wall_clock_compatible','generation_clock_compatible','initial_notice_first_cycle'}
    strings={'case','scenario','budget_level','method','window_feasibility'}
    return [{k:(None if v=='' else int(v) if k in ints else v=='True' if k in flags else v if k in strings else float(v))
             for k,v in r.items()} for r in table]


def check_selection(output, table):
    import math
    from run_matched_reference import GUARDED, PARAMETERS, plain
    from rolling_workloads import budget_for
    plan=load(output/'calibration-plan.json')
    scores=load(output/'parameter-scores.json');winners=load(output/'frozen-parameters.json')
    same(len(scores),18,'完整十八参数评分');same(len(winners),3,'三个全局参数')
    values=typed_table(table);count=2
    for method in GUARDED:
        eligible=[]
        for index in range(6):
            group=[r for r in values if (r['method'],r['parameter_index'])==(method,index)]
            expected={(e['scenario'],e['budget_level'],e['seed']) for e in plan['execution_order'] if e['method']==method and e['parameter_index']==index}
            same({(r['scenario'],r['budget_level'],r['seed']) for r in group},expected,'全部八例四场景双业务')
            same(len(group),8,'每预设完整分母')
            excess=[]
            valid=all(type(r[k]) in (int,float) and math.isfinite(r[k]) for r in group for k in ('controller_loss_lower','controller_loss_upper'))
            valid=valid and all(0<=r['controller_loss_lower']<=r['controller_loss_upper']<=4+1e-10 for r in group)
            for r in group:
                budget=budget_for(r['scenario'],r['budget_level'])
                limits=dict(budget.link_bytes_per_s,export=budget.export_bytes_per_s,api_serialized=budget.control_bytes_per_s,local_notice_payload=128)
                actual={k:r[k+'_upper_bytes_per_s'] for k in ('e1','e2','e3','export')}
                actual.update(api_serialized=r['api_bytes_per_s'],local_notice_payload=r['local_notice_bytes_per_s'])
                known=all(type(v) in (int,float) and math.isfinite(v) and v>=0 for v in actual.values())
                valid=valid and known
                excess.append(sum(max(actual[k]/limits[k]-1,0) for k in limits) if known else None)
            rank=[sum(r['window_feasibility']!='within' for r in group),sum(excess),
                  sum(r['controller_loss_upper'] for r in group)/8,sum(r['successful_updates'] for r in group),index] if valid else None
            score=next(s for s in scores if (s['method'],s['parameter_index'])==(method,index))
            same(score['rank'],rank,'独立费用与控制器目标全局排序')
            same(score['evaluable'],valid,'完整有限评分');same(score['feasible'],valid and rank[0]==0,'单例预算失败保留')
            same(score['case_names'],[r['case'] for r in group],'全部评分案例')
            same(score['window_statuses'],{r['case']:r['window_feasibility'] for r in group},'全部费用状态')
            same(score['adaptive'],plain(PARAMETERS[index]),'预声明参数值');same(score['case_count'],8,'完整分母')
            if valid:eligible.append((rank,index))
            count+=10
        chosen=min(eligible);winner=next(w for w in winners if w['method']==method)
        same(winner['selected_parameter'],chosen[1],'独立全局选择')
        same(winner['selected_rank'],chosen[0],'完整选择排序')
        same(winner['adaptive'],plain(PARAMETERS[chosen[1]]),'实际冻结参数')
        same(winner['status'],'frozen' if chosen[0][0]==0 else 'frozen_with_budget_failures','实际冻结状态')
        same(winner['feasible_parameters'],sum(v[0][0]==0 for v in eligible),'共同可行预设数')
        same(winner['primary_observer'],'controller','冻结控制器主目标');count+=6
    same(load(output/'findings.json')['selected_parameters'],{r['method']:r['selected_parameter'] for r in winners},'全局选择汇总')
    return count+1


def check_pairs(output,cases,table):
    from collections import Counter
    from diagnose_observer_training import business_packet
    signatures,workloads={},{}
    for c in cases:
        d=output/c['name'];packets=load(d/'packets.json');ids={p['packet_id']:i for i,p in enumerate(packets)}
        key=c['scenario'],c['seed'];business=[business_packet(p) for p in packets],load(d/'tasks.json')
        if key in workloads: same(business,workloads[key],'共同业务提供与任务')
        workloads[key]=business
        signatures[c['name']]=sorted([ids[r['packet_id']],x['switch_id']] for r in rows(d/'collector.jsonl') if r.get('ether_type')==0x88b6 for x in r['records'])
    same(load(output/'sample-signatures.json'),signatures,'完整184例实际采样位置')
    frozen={r['method']:r['selected_parameter'] for r in load(output/'frozen-parameters.json')}
    selected=[r for r in typed_table(table) if r['method'] not in frozen or r['parameter_index']==frozen[r['method']]]
    same(len(selected),64,'冻结后八方法完整64例');same(load(output/'selected-cases.json'),selected,'全部选中案例')
    pairs=load(output/'paired-cases.json');groups=load(output/'paired-groups.json')
    same(len(pairs),56,'全部动态静态配对');same(len(groups),28,'全部场景方法组')
    count=len(cases)+5
    expected=[]
    layers=('e1','e2','e3','export','api_serialized','local_notice_payload')
    for d in selected:
        if d['method']=='static':continue
        b=next(r for r in selected if r['method']=='static' and (r['scenario'],r['budget_level'],r['seed'])==(d['scenario'],d['budget_level'],d['seed']))
        p=next(p for p in pairs if p['dynamic_case']==d['case'])
        same(p['static_case'],b['case'],'同业务静态参照');expected.append(d['case'])
        same(p['both_within'],all(r['window_feasibility']=='within' for r in (b,d)),'配对双方达标')
        same(p['same_sample_positions'],signatures[d['case']]==signatures[b['case']],'实际采样位置匹配')
        for observer in ('collector','controller'):
            a=[d[observer+'_loss_'+k] for k in ('lower','upper')];v=[b[observer+'_loss_'+k] for k in ('lower','upper')]
            delta=[a[0]-v[1],a[1]-v[0]]
            same(p[observer+'_dynamic_bounds'],a,'动态年龄');same(p[observer+'_static_bounds'],v,'静态年龄')
            same(p[observer+'_difference_bounds'],delta,'同业务条件差值')
            same(p[observer+'_direction'],'lower' if delta[1]<0 else 'higher' if delta[0]>0 else 'overlap','条件方向')
        for kind,r in (('dynamic',d),('static',b)):
            actual={k:[r[k+'_'+s+'_bytes_per_s'] for s in ('lower','upper')] for k in layers[:4]}
            actual.update(api_serialized=[r['api_bytes_per_s']]*2,local_notice_payload=[r['local_notice_bytes_per_s']]*2)
            same(p[kind+'_fee_rates'],actual,'完整六层实际配对费用')
        count+=13
    same([p['dynamic_case'] for p in pairs],expected,'完整配对顺序')
    for g in groups:
        values=[p for p in pairs if (p['scenario'],p['budget_level'],p['method'])==(g['scenario'],g['budget_level'],g['method'])]
        same(sorted(p['seed'] for p in values),[1301,1423],'每组两条完整校准业务')
        same(g['case_count'],2,'独立业务分母');same(g['both_within_pairs'],sum(p['both_within'] for p in values),'达标分母')
        same(g['same_sample_position_pairs'],sum(p['same_sample_positions'] for p in values),'位置相同分母')
        for observer in ('collector','controller'):
            same(g[observer+'_both_eligible_lower'],all(p['both_within'] and p[observer+'_direction']=='lower' for p in values),'共同局部改善组')
            same(g[observer+'_directions'],dict(Counter(p[observer+'_direction'] for p in values)),'全部方向')
            for key in ('dynamic_bounds','static_bounds','difference_bounds'):
                same(g[observer+'_mean_'+key],[sum(p[observer+'_'+key][i] for p in values)/2 for i in (0,1)],'完整双业务均值')
        for kind in ('dynamic','static'):
            same(g[kind+'_mean_fee_rates'],{k:[sum(p[kind+'_fee_rates'][k][i] for p in values)/2 for i in (0,1)] for k in layers},'完整分层费用均值')
        count+=16
    return count+1


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--analysis', type=Path, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args(); result = check(a.analysis)
    if a.output: a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
