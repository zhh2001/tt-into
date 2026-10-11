"""独立复核有限动态网络校准的费用、年龄及全局参数排序。"""
import argparse
import csv
import json
from pathlib import Path

from check_matched_network import load, rows, sha, charges_for
from check_dynamic_network_calibration import audit
from network_entry_bounds import output_frames, costs, generation_bounds
from check_rolling_reference import near, same, clipped_loss


def table(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def check_summary(output, cases):
    base = {r['case']: r for r in table(output / 'summary.csv')}
    case_rows = {r['name']: r for r in table(output / 'case-availability.csv')}
    aggregate = table(output / 'block-aggregate.csv')
    available = table(output / 'report-availability.csv')
    batches = table(output / 'availability-batches.csv')
    notices = table(output / 'notice-availability.csv')
    same(set(base), {c['name'] for c in cases}, '案例汇总分母')
    same(set(case_rows), set(base), '可用性案例分母')
    count = 0
    outputs = output_frames(output, cases)
    rpc = rows(output/'rpc-operations.jsonl')
    for c in cases:
        directory = output / c['name']
        cost = costs(directory, c, outputs[c['name']], charges_for(directory, rpc))
        same(load(directory/'window-costs.json'), cost, '费用由真实输出和调用重建')
        same(base[c['name']]['window_feasibility'], cost['window_feasibility'], '窗口可行性来源')
        age = generation_bounds(directory, c)
        same(load(directory/'generation-bounds.json'), age, '生成时间及完整任务重建')
        for side in ('lower','upper'):
            near(float(base[c['name']]['loss_'+side]), age['loss_'+side], '采集端年龄汇总来源')
        opts = load(directory / 'algorithm-settings.json')
        if cost['measurement_upper_bytes'] is None:
            same(base[c['name']]['normalized_excess_upper'], '', '不可评价费用不作零超限')
        else:
            limits = dict(opts['budget']['link_bytes_per_s'], export=opts['budget']['export_bytes_per_s'],
                          api_serialized=opts['budget']['control_bytes_per_s'], local_notice_payload=opts['notice_budget_bytes_per_s'])
            amounts = dict(cost['measurement_upper_bytes'], api_serialized=cost['api_serialized_bytes'], local_notice_payload=cost['local_notice_payload_bytes'])
            excess = sum(max(amounts[k] / (8 * limits[k]) - 1, 0) for k in ('e1', 'e2', 'e3', 'export', 'api_serialized', 'local_notice_payload'))
            near(float(base[c['name']]['normalized_excess_upper']), excess, '实际逐层归一超限独立来源')
        count += 6
        decisions = [d for d in rows(directory / 'decisions.jsonl') if 'view' in d]
        rs = [r for r in available if r['name'] == c['name']]
        bs = [r for r in batches if r['name'] == c['name']]
        ns = [r for r in notices if r['name'] == c['name']]
        same(len(bs), len(decisions), '完整批次表')
        same(len(rs), sum(d['availability']['reports_after'] - d['availability']['reports_before'] for d in decisions), '进入视图的合法报告分母')
        physical = rows(directory / 'collector.jsonl')
        for r in rs:
            actual = next(x for x in physical if x.get('ether_type') == 0x88b6 and str(x['packet_id']) == r['packet_id'])
            same(int(r['received_ns']), actual['receive_monotonic_ns'], '汇总实际收报来源')
            decision = next(d for d in decisions if str(d['scheduled_offset_us']) == r['scheduled_offset_us'])
            same(int(r['entered_lower_ns']), decision['availability']['ingest_started_ns'], '进入状态的时间下界')
            same(int(r['entered_upper_ns']), decision['availability']['available_ns'], '进入状态的时间上界')
            same(int(r['first_cutoff_ns']), decision['cutoff_ns'], '首次视图截止')
            near(float(r['receipt_to_entry_upper_ms']), (int(r['entered_upper_ns']) - int(r['received_ns'])) / 1e6, '收报至可用时间')
            count += 5
        generation = load(directory / 'generation-bounds.json')
        earlier, later = [], []
        for r in rs:
            frame = next(x for x in physical if x.get('ether_type') == 0x88b6 and str(x['packet_id']) == r['packet_id'])
            for record in frame['records']:
                interval = next(x for x in generation['intervals'] if x['packet_id'] == frame['packet_id'] and x['node'] == record['switch_id'])
                obj = {'node': record['switch_id'], 'port': 2, 'queue': 0}
                earlier.append({'object_id': obj, 'generated_us': (interval['generation_upper_ns'] - c['origin_monotonic_ns']) / 1000,
                                'received_us': (int(r['entered_lower_ns']) - c['origin_monotonic_ns']) / 1000})
                later.append({'object_id': obj, 'generated_us': (interval['generation_lower_ns'] - c['origin_monotonic_ns']) / 1000,
                              'received_us': (int(r['entered_upper_ns']) - c['origin_monotonic_ns']) / 1000})
        bounds = load(directory / 'controller-visible-bounds.json')
        for side, sample in (('lower', earlier), ('upper', later)):
            loss = clipped_loss(load(directory / 'tasks.json'), sample, 4)[0]
            near(bounds['loss_' + side], loss, '控制器视图损失独立重建')
            near(float(case_rows[c['name']]['controller_loss_' + side]), loss, '视图损失汇总来源')
            count += 2
        for b, d in zip(bs, decisions):
            e = d['availability']
            near(float(b['read_ms']), (e['read_finished_ns'] - e['read_started_ns']) / 1e6, '完整读取耗时')
            near(float(b['ingest_ms']), (e['available_ns'] - e['ingest_started_ns']) / 1e6, '状态更新耗时')
            count += 2
        actual_notices = rows(directory / 'notices.jsonl')
        same(len(ns), len(actual_notices), '完整通知表')
        for n, actual in zip(ns, actual_notices):
            same(int(n['recv_finished_ns']), actual['received_ns'], '通知实际可用')
            first = next(d for d in decisions if any(v['version'] == actual['message']['version'] for v in d['view']['notifications']))
            same(int(n['first_cycle_offset_us']), first['scheduled_offset_us'], '通知首次进入周期')
            count += 2
        first_cycle = bool(ns and int(ns[0]['first_cycle_offset_us']) == 0)
        same(case_rows[c['name']]['initial_notice_first_cycle'], str(first_cycle), '首周期通知分组')
        count += 4
    groups = {(c['scenario'], c['budget_level'], (c['method'], c['parameter_index'])) for c in cases}
    same({(r['scenario'], r['budget_level'], (r['method'], int(r['parameter_index']) if r['parameter_index'] else None)) for r in aggregate}, groups, '完整汇总单元')
    same(len(aggregate), len(groups), '无重复汇总单元')
    for a in aggregate:
        members = [c['name'] for c in cases if (c['scenario'], c['budget_level'], (c['method'], c['parameter_index'])) == (a['scenario'], a['budget_level'], (a['method'], int(a['parameter_index']) if a['parameter_index'] else None))]
        same(int(a['case_count']), len(members), '完整案例独立重复分母')
        same(int(a['business_seed_count']), len({c['seed'] for c in cases if c['name'] in members}), '真实业务种子分母')
        lower, upper = [float(base[n]['loss_lower']) for n in members], [float(base[n]['loss_upper']) for n in members]
        near(float(a['loss_lower_mean']), sum(lower) / len(lower), '损失下界均值')
        near(float(a['loss_upper_mean']), sum(upper) / len(upper), '损失上界均值')
        near(float(a['loss_observed_lower_min']), min(lower), '最小损失界')
        near(float(a['loss_observed_upper_max']), max(upper), '最大损失界')
        midpoints = [(x + y) / 2 for x, y in zip(lower, upper)]
        near(float(a['loss_midpoint_case_range']), max(midpoints) - min(midpoints), '案例间变异')
        for side in ('lower', 'upper'):
            near(float(a['controller_loss_' + side + '_mean']), sum(float(case_rows[n]['controller_loss_' + side]) for n in members) / len(members), '完整案例视图损失均值')
        same(int(a['within_cases']), sum(base[n]['window_feasibility'] == 'within' for n in members), '达标案例分母')
        same(int(a['initial_notice_first_cycle_cases']), sum(case_rows[n]['initial_notice_first_cycle'] == 'True' for n in members), '通知分组分母')
        count += 9
    count += check_selection(output, base)
    return {'status': 'passed', 'checks': count, 'cases': len(cases), 'groups': len(groups), 'report_rows': len(available)}


def check_selection(output, base):
    import math
    plan = load(output / 'calibration-plan.json')
    scores = load(output / 'parameter-scores.json')
    frozen = load(output / 'frozen-parameters.json')
    same(len(base), 184, '完整动态校准案例分母')
    same(len(scores), 18, '三方法各六评分')
    same(len(frozen), 3, '全局参数冻结分母')
    expected_scores, expected_frozen, count = [], [], 0
    for method in ('uniform_guard', 'phase_guard', 'phase_local_guard'):
        eligible = []
        for index in range(6):
            values = [r for r in base.values() if r['method'] == method and r['parameter_index'] == str(index)]
            same({(r['scenario'], r['budget_level'], int(r['seed'])) for r in values},
                 {(p['scenario'], p['budget_level'], s) for p in plan['profiles'] for s in (1301, 1423)}, '评分八例完整场景和业务')
            same(len(values), 8, '全局评分八例且无重复')
            valid = all(r[k] not in ('', 'None') and math.isfinite(float(r[k])) for r in values for k in ('loss_lower', 'loss_upper', 'normalized_excess_upper'))
            valid = valid and all(0 <= float(r['loss_lower']) <= float(r['loss_upper']) <= 4 + 1e-10 and float(r['normalized_excess_upper']) >= 0 for r in values)
            rank = [sum(r['window_feasibility'] != 'within' for r in values), sum(float(r['normalized_excess_upper']) for r in values),
                    sum(float(r['loss_upper']) for r in values) / 8, sum(int(r['successful_updates']) for r in values), index] if valid else None
            adaptive = next(p['adaptive'] for p in plan['presets'] if (p['method'], p['parameter_index']) == (method, index))
            expected = dict(method=method, parameter_index=index, adaptive=adaptive, case_count=8,
                evaluable=valid, feasible=valid and rank[0] == 0, rank=rank,
                case_names=[r['case'] for r in values], window_statuses={r['case']: r['window_feasibility'] for r in values})
            same([r for r in scores if (r['method'], r['parameter_index']) == (method, index)], [expected], '全局评分独立重建')
            expected_scores.append(expected)
            if valid: eligible.append(expected)
            count += 4
        chosen = min(eligible, key=lambda r: r['rank']) if eligible else None
        expected = dict(method=method, selected_parameter=chosen['parameter_index'] if chosen else None,
            adaptive=chosen['adaptive'] if chosen else None, selected_rank=chosen['rank'] if chosen else None,
            feasible_parameters=sum(r['feasible'] for r in eligible),
            status=('frozen' if chosen['feasible'] else 'frozen_with_budget_failures') if chosen else 'unavailable')
        same([r for r in frozen if r['method'] == method], [expected], '全局排序及失败冻结语义')
        expected_frozen.append(expected)
        count += 1
    choices = {r['method']: r['selected_parameter'] for r in expected_frozen if r['status'] != 'unavailable'}
    selected = load(output / 'selected-calibration-cases.json')
    names = [r['case'] for r in base.values() if r['parameter_index'] == '' or int(r['parameter_index']) == choices.get(r['method'], -1)]
    same([r['case'] for r in selected], names, '选择展示包含固定方法和全局参数完整案例')
    for r in selected:
        actual = base[r['case']]
        same(set(r), set(actual), '展示完整字段')
        for k, v in r.items():
            if v is None: same(actual[k], '', '展示空值来源')
            elif isinstance(v, float): near(float(actual[k]), v, '展示数值来源')
            else: same(actual[k], str(v), '展示字段来源')
        count += len(r)
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    output = parser.parse_args().analysis
    audit(output)
    manifest = load(output / 'manifest.json')
    for name, digest in manifest['analysis_source_sha256'].items():
        same(sha(output / 'executed-analysis-source' / name), digest, '分析实际源码')
    print(json.dumps(check_summary(output, manifest['cases']), ensure_ascii=False))
