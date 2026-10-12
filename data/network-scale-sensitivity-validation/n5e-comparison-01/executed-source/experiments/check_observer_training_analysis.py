"""独立验收有限静态训练的批次、两位置积分、完整排序和六层前沿。"""
import argparse
import csv
import json
from pathlib import Path

from check_observer_training import audit
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
    return {'status': 'passed', 'cases': len(table), 'tasks': task_count, 'controller_samples': samples_total,
            'selection_checks': selection_checks,
            'analysis_checks': count + 3, 'network_checks': sum(verified[k] for k in ('checks', 'live_control_checks', 'observer_checks', 'cache_and_budget_checks')),
            'analysis_manifest_sha256': sha(output / 'manifest.json')}


def check_selection(output, table):
    plan = load(output / 'training-plan.json')
    candidates = load(output / 'candidate-plan.json')
    scores, winners = load(output / 'candidate-scores.json'), load(output / 'training-selections.json')
    layers = ('e1', 'e2', 'e3', 'export', 'api_serialized', 'local_notice_payload')
    expected, selected, count = [], [], 0
    same(len(scores), 96, '完整96个候选评分')
    for group in candidates['groups']:
        ranked = []
        for candidate in group['candidates']:
            values = [r for r in table if r['scenario'] == group['scenario'] and r['budget_level'] == group['budget_level']
                      and int(r['candidate_index']) == candidate['candidate_index']]
            same(sorted(int(r['seed']) for r in values), [11, 29], '排序只用完整双训练业务')
            uppers = [float(r['controller_loss_upper']) for r in values]
            score = next(s for s in scores if (s['scenario'], s['candidate_index']) == (group['scenario'], candidate['candidate_index']))
            same(score['config'], candidate['config'], '评分配置来自既有prepared候选')
            rank = [max(uppers), sum(uppers)/2, sum(v is not None for v in candidate['config']), [v or 0 for v in candidate['config']]]
            same(score['rank'], rank, '独立控制器最坏上界排序')
            fields = [k + '_upper_bytes_per_s' for k in layers[:4]] + ['api_bytes_per_s', 'local_notice_bytes_per_s']
            cost_valid = all(r[field] != '' for r in values for field in fields)
            feasible = cost_valid and all(r['window_feasibility'] == 'within' for r in values)
            same(score['feasible'], feasible, '一例失败不能入选')
            if not cost_valid:
                same(score['worst_rates'], None, '未知费用不填零')
                same(score['frontier_vector'], None, '未知费用不产生前沿向量')
                same(score['frontier_evaluable'], False, '未知费用保留而不作前沿排名')
            for layer in layers if cost_valid else ():
                field = layer + '_upper_bytes_per_s' if layer in layers[:4] else 'api_bytes_per_s' if layer == 'api_serialized' else 'local_notice_bytes_per_s'
                near(score['worst_rates'][layer], max(float(r[field]) for r in values), '费用前沿使用最坏业务和实际六层费用')
            vector = [rank[0]] + [score['worst_rates'][k] for k in layers] if cost_valid else None
            same(score['frontier_vector'], vector, '完整七维前沿向量')
            expected.append((group['scenario'], candidate['candidate_index'], vector))
            if feasible: ranked.append((rank, candidate['candidate_index']))
            count += 12
        winner = next(r for r in winners if r['scenario'] == group['scenario'])
        chosen = min(ranked)[1] if ranked else None
        same(winner['selected_candidate'], chosen, '独立完整排序选择')
        same(winner['feasible_candidates'], len(ranked), '共同可行候选分母')
        if chosen is not None:
            same(winner['selected_config'], group['candidates'][chosen]['config'], '训练选中阈值')
            same(winner['status'], 'training_selected_pending_confirmation', '选择不能提前称共同初始化冻结')
        selected.append(winner); count += 4
    for score in scores:
        vector = score['frontier_vector']
        dominates = [index for scenario, index, values in expected if scenario == score['scenario']
                     and vector is not None and values is not None and all(a <= b for a, b in zip(values, vector)) and any(a < b for a, b in zip(values, vector))]
        same(score['dominated_by'], dominates, '独立完整六层支配关系')
        same(score['finite_subset_nondominated'], vector is not None and not dominates, '有限子集非支配身份')
        count += 2
    same(load(output / 'finite-frontier.json'), [r for r in scores if r['finite_subset_nondominated']], '前沿保留所有非支配点，包括费用失败')
    findings = load(output / 'findings.json')
    same(findings['selected_candidates'], {r['scenario']: r['selected_candidate'] for r in winners}, '完整选择汇总')
    return count + 2


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--analysis', type=Path, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args(); result = check(a.analysis)
    if a.output: a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
