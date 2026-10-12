"""从案例和真实周期重新核对可用性表及分块汇总。"""
import argparse
import csv
import json
from pathlib import Path

from check_matched_network import load, rows, sha
from check_network_calibration import audit
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
    for c in cases:
        directory = output / c['name']
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
    groups = {(c['scenario'], c['budget_level'], c['method']) for c in cases}
    same({(r['scenario'], r['budget_level'], r['method']) for r in aggregate}, groups, '完整汇总单元')
    same(len(aggregate), len(groups), '无重复汇总单元')
    for a in aggregate:
        members = [c['name'] for c in cases if (c['scenario'], c['budget_level'], c['method']) == (a['scenario'], a['budget_level'], a['method'])]
        same(int(a['case_count']), len(members), '完整案例独立重复分母')
        same(int(a['business_seed_count']), 1, '排序随机种子不增加业务分母')
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
    return {'status': 'passed', 'checks': count, 'cases': len(cases), 'groups': len(groups), 'report_rows': len(available)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    output = parser.parse_args().analysis
    audit(output)
    manifest = load(output / 'manifest.json')
    for name, digest in manifest['analysis_source_sha256'].items():
        same(sha(output / 'executed-analysis-source' / name), digest, '分析实际源码')
    print(json.dumps(check_summary(output, manifest['cases']), ensure_ascii=False))
