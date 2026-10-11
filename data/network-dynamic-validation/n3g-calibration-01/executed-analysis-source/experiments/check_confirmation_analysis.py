"""独立复核三块静态确认的费用、年龄、位置与冻结选择。"""
import argparse
import csv
import json
from pathlib import Path

from check_matched_network import load, rows, sha, charges_for
from check_static_confirmation import audit
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
        count += 5
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
    groups = {(c['scenario'], c['budget_level'], c['candidate_index']) for c in cases}
    same({(r['scenario'], r['budget_level'], int(r['candidate_index'])) for r in aggregate}, groups, '完整汇总单元')
    same(len(aggregate), len(groups), '无重复汇总单元')
    for a in aggregate:
        members = [c['name'] for c in cases if (c['scenario'], c['budget_level'], c['candidate_index']) == (a['scenario'], a['budget_level'], int(a['candidate_index']))]
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
    count += check_repeats(output, cases)
    return {'status': 'passed', 'checks': count, 'cases': len(cases), 'groups': len(groups), 'report_rows': len(available)}


def check_selection(output, base):
    import math
    plan = load(output / 'confirmation-plan.json')
    scores = load(output / 'confirmation-scores.json')
    frozen = load(output / 'frozen-static.json')
    same(len(base), 36, '完整确认评分分母')
    same(len(scores), 6, '六配置评分分母')
    same(len(frozen), 4, '四场景冻结分母')
    checks = 0
    expected_scores = []
    for cell in plan['unique_config_cells']:
        identity = cell['scenario'], cell['budget_level'], cell['candidate_index']
        values = [r for r in base.values() if (r['scenario'], r['budget_level'], int(r['candidate_index'])) == identity]
        same(sorted((int(r['seed']), int(r['block'])) for r in values), [(s, b) for s in (11, 29) for b in (1, 2, 3)], '六例业务和块身份')
        bounds = {f"{r['seed']}:{r['block']}": [float(r['loss_lower']), float(r['loss_upper'])] for r in values}
        valid = all(math.isfinite(v) for bs in bounds.values() for v in bs) and all(0 <= lo <= hi <= 4 + 1e-10 for lo, hi in bounds.values())
        feasible = valid and all(r['window_feasibility'] == 'within' for r in values)
        cfg = cell['config']
        rank = [max(b[1] for b in bounds.values()), sum(b[1] for b in bounds.values()) / 6,
                sum(v is not None for v in cfg), [v or 0 for v in cfg]] if valid else None
        expected = dict(cell, case_count=6, feasible=feasible, age_evaluable=valid, rank=rank,
                        window_statuses={f"{r['seed']}:{r['block']}": r['window_feasibility'] for r in values}, collector_loss_bounds=bounds)
        same([r for r in scores if (r['scenario'], r['budget_level'], r['candidate_index']) == identity], [expected], '完整资格与评分独立重建')
        expected_scores.append(expected)
        checks += 4
    for s, b in (('stable', 'high'), ('shared_priority', 'high'), ('switch', 'low'), ('bursty_jitter', 'low')):
        eligible = [r for r in expected_scores if (r['scenario'], r['budget_level']) == (s, b) and r['feasible']]
        winner = sorted(eligible, key=lambda r: r['rank'])[0] if eligible else None
        expected = dict(scenario=s, budget_level=b, feasible_candidates=len(eligible),
            selected_candidate=winner['candidate_index'] if winner else None, selected_config=winner['config'] if winner else None,
            selected_rank=winner['rank'] if winner else None, status='frozen' if winner else 'finite_subset_infeasible')
        same([r for r in frozen if (r['scenario'], r['budget_level']) == (s, b)], [expected], '六例最坏上界冻结选择')
        checks += 1
    return checks


def check_repeats(output, cases):
    signatures = load(output / 'sample-signatures.json')
    same(set(signatures), {c['name'] for c in cases}, '完整采样位置分母')
    workloads, bounds, checks = {}, {}, 0
    for c in cases:
        directory = output / c['name']
        packets = load(directory / 'packets.json')
        index = {p['packet_id']: i for i, p in enumerate(packets)}
        same(len(index), len(packets), '包序号唯一')
        positions = []
        for report in rows(directory / 'collector.jsonl'):
            if report.get('ether_type') == 0x88b6:
                ordinal = index[report['packet_id']]
                for r in report['records']:
                    same(packets[ordinal]['path'][r['hop'] - 1], r['switch_id'], '实际采样路径')
                    positions.append([ordinal, packets[ordinal]['flow'], r['switch_id'], r['hop']])
                    checks += 1
        same(len(set(map(tuple, positions))), len(positions), '样本位置没有重复')
        same(signatures[c['name']], sorted(positions), '样本位置由原始报告重建')
        workload = [{k: p[k] for k in ('flow', 'offset_ns', 'ip_length', 'path')} for p in packets]
        key = c['scenario'], c['seed']
        full = workload, load(directory / 'tasks.json')
        if key in workloads:
            same(full, workloads[key], '同业务配置和部署共享完整输入')
        workloads[key] = full
        age = load(directory / 'generation-bounds.json')
        bounds[c['name']] = [age['loss_lower'], age['loss_upper']]
        checks += 4
    repeats = load(output / 'seed-repeat-diagnostics.json')
    identities = {(c['scenario'], c['budget_level'], c['candidate_index'], c['seed']) for c in cases}
    same(len(repeats), 12, '三部署重复组分母')
    same({(r['scenario'], r['budget_level'], r['candidate_index'], r['seed']) for r in repeats}, identities, '重复组身份')
    for r in repeats:
        members = sorted([c for c in cases if (c['scenario'], c['budget_level'], c['candidate_index'], c['seed']) ==
                         (r['scenario'], r['budget_level'], r['candidate_index'], r['seed'])], key=lambda c: c['block'])
        same([c['block'] for c in members], [1, 2, 3], '重复块完整')
        names = [c['name'] for c in members]
        ps, bs = [signatures[n] for n in names], [bounds[n] for n in names]
        mids = [(lo + hi) / 2 for lo, hi in bs]
        expected = dict(scenario=r['scenario'], budget_level=r['budget_level'], candidate_index=r['candidate_index'], seed=r['seed'],
            cases=names, sample_counts=[len(p) for p in ps], same_sampling_positions=ps[0] == ps[1] == ps[2],
            differences_from_first=[len(set(map(tuple, ps[0])) ^ set(map(tuple, p))) for p in ps],
            loss_bounds=bs, loss_midpoint_range=max(mids) - min(mids),
            loss_intervals_common_overlap=max(lo for lo, _ in bs) <= min(hi for _, hi in bs))
        same(r, expected, '完整同业务重复诊断')
        checks += 2
    pairs = load(output / 'candidate-repeat-pairs.json')
    same(len(pairs), 12, '两个变更场景的候选配对分母')
    same({(r['scenario'], r['seed'], r['block']) for r in pairs}, {(s, seed, b) for s in ('shared_priority', 'bursty_jitter') for seed in (11, 29) for b in (1, 2, 3)}, '完整候选配对身份')
    for r in pairs:
        model, alternative = r['model_case'], r['alternative_case']
        for name, role in ((model, 'frozen_model_reference'), (alternative, 'network_selected')):
            c = next(c for c in cases if c['name'] == name)
            same((c['scenario'], c['budget_level'], c['seed'], c['block']), (r['scenario'], r['budget_level'], r['seed'], r['block']), '候选配对来自同业务同块')
            if role not in c['roles']: raise ValueError('候选配对角色错误')
        a, b = bounds[model], bounds[alternative]
        same(r, dict(scenario=r['scenario'], budget_level=r['budget_level'], seed=r['seed'], block=r['block'],
            model_case=model, alternative_case=alternative, same_sampling_positions=signatures[model] == signatures[alternative],
            position_symmetric_difference=len(set(map(tuple, signatures[model])) ^ set(map(tuple, signatures[alternative]))),
            model_loss_bounds=a, alternative_loss_bounds=b, loss_intervals_overlap=max(a[0], b[0]) <= min(a[1], b[1]),
            alternative_minus_model_bounds=[b[0] - a[1], b[1] - a[0]]), '候选差值条件区间独立重建')
        checks += 3
    return checks


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    output = parser.parse_args().analysis
    audit(output)
    manifest = load(output / 'manifest.json')
    for name, digest in manifest['analysis_source_sha256'].items():
        same(sha(output / 'executed-analysis-source' / name), digest, '分析实际源码')
    print(json.dumps(check_summary(output, manifest['cases']), ensure_ascii=False))
