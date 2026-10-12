"""直接从80例汇总和真实采样/配置来源核对104配对与各级分母。"""
from collections import Counter
from check_observation_wire import load, rows
from check_rolling_reference import same as strict_same
from diagnose_observer_training import business_packet


def same(a, b, name='敏感性独立配对核对'):
    strict_same(a, b, name)


def check_records(summary, plan, data, signatures, decisions):
    checks = 0
    cases = {r['batch_id'] + '/' + r['case']: r for r in summary}
    by_id = {(r['condition_id'], r['business_seed'], r['block'], r['method']): name for name, r in cases.items()}
    same(Counter(by_id.keys()), Counter((e['condition_id'], e['business_seed'], e['block'], e['method']) for e in plan['execution_order']), '完整实际身份')
    same(len(cases), 80, '全部正式案例'); checks += 2
    expected = {}
    for seed in plan['business_seeds']:
        for block in plan['blocks']:
            for condition in ('B085', 'B100', 'B115', 'O250', 'O1000'):
                expected['method', condition, 'phase_guard', seed, block] = (by_id[condition, seed, block, 'phase_guard'], by_id[condition, seed, block, 'static'])
                if condition != 'B100':
                    for method in ('static', 'phase_guard'):
                        expected['condition', condition, method, seed, block] = (by_id[condition, seed, block, method], by_id['B100', seed, block, method])
    pairs = data['paired_cases']
    same(Counter((p['relation'], p['condition_id'], p['method'], p['business_seed'], p['block']) for p in pairs), Counter(expected.keys()), '104真实配对身份'); checks += 1
    for p in pairs:
        key = p['relation'], p['condition_id'], p['method'], p['business_seed'], p['block']
        a, b = expected[key]; x, z = cases[a], cases[b]
        same((p['left_case'], p['right_case']), (a, b), '实际条件配对指向')
        same(p['both_within'], x['window_feasibility'] == z['window_feasibility'] == 'within', '逐例费用资格')
        same(p['same_sample_positions'], signatures[a] == signatures[b], '实际采样位置'); checks += 3
        for side, row in (('left', x), ('right', z)):
            same(p[side + '_feasibility'], row['window_feasibility'], '保留预算状态')
            same(p[side + '_fee_rates'], row['fee_rates'], '完整费用及空值')
            same(p[side + '_coverage'], row['weighted_active_interval_coverage'], '全部任务覆盖'); checks += 3
        for observer in ('controller', 'collector'):
            lo, hi = x[observer + '_loss']; low, high = z[observer + '_loss']
            diff = [lo - high, hi - low]
            same(p[observer + '_left_bounds'], [lo, hi], '配对左年龄')
            same(p[observer + '_right_bounds'], [low, high], '配对右年龄')
            same(p[observer + '_difference_bounds'], diff, '独立条件差值')
            same(p[observer + '_direction'], 'lower' if hi < low else 'higher' if lo > high else 'overlap', '条件方向')
            same(p[observer + '_midpoint_relative_reduction'], (sum([low, high]) - sum([lo, hi])) / sum([low, high]) if low + high > 0 else None, '实际相对中点'); checks += 5

    def average(values):
        return [None if any(v[i] is None for v in values) else sum(v[i] for v in values) / len(values) for i in (0, 1)]

    for field, keys, count, deployments, businesses in (
        ('business_pairs', ('relation', 'condition_id', 'method', 'business_seed'), 52, 2, 1),
        ('paired_groups', ('relation', 'condition_id', 'method'), 13, 8, 4)):
        saved = data[field]
        identities = {tuple(p[k] for k in keys) for p in pairs}
        same(Counter(tuple(g[k] for k in keys) for g in saved), Counter(identities)); same(len(saved), count); checks += 2
        for g in saved:
            members = [p for p in pairs if all(p[k] == g[k] for k in keys)]
            same(len(members), deployments); same(g['deployment_pairs'], deployments); same(g['business_count'], businesses)
            same(g['both_within_pairs'], sum(p['both_within'] for p in members)); same(g['same_sample_position_pairs'], sum(p['same_sample_positions'] for p in members)); checks += 5
            for side in ('left', 'right'):
                actual = [cases[p[side + '_case']] for p in members]
                same(g[side + '_mean_coverage'], sum(r['weighted_active_interval_coverage'] for r in actual) / deployments)
                same(g[side + '_mean_fee_rates'], {k: average([r['fee_rates'][k] for r in actual]) for k in actual[0]['fee_rates']}); checks += 2
            for observer in ('controller', 'collector'):
                xs = [cases[p['left_case']][observer + '_loss'] for p in members]
                zs = [cases[p['right_case']][observer + '_loss'] for p in members]
                x, z = average(xs), average(zs)
                same(g[observer + '_mean_left_bounds'], x); same(g[observer + '_mean_right_bounds'], z)
                same(g[observer + '_mean_difference_bounds'], average([[a[0]-b[1], a[1]-b[0]] for a, b in zip(xs, zs)]))
                same(g[observer + '_midpoint_relative_reduction'], (sum(z) - sum(x)) / sum(z) if sum(z) > 0 else None)
                same(g[observer + '_directions'], dict(Counter(p[observer + '_direction'] for p in members))); checks += 5
    repeats = data['deployment_repeats']
    repeat_keys = {(r['condition_id'], r['business_seed'], r['method']) for r in summary}
    same(Counter((r['condition_id'], r['business_seed'], r['method']) for r in repeats), Counter(repeat_keys)); checks += 1
    for r in repeats:
        names = [by_id[r['condition_id'], r['business_seed'], block, r['method']] for block in plan['blocks']]
        same(r['cases'], names); same(r['feasibility'], [cases[n]['window_feasibility'] for n in names])
        same(r['same_sample_positions'], signatures[names[0]] == signatures[names[1]])
        same(r['same_decision_sequence'], decisions[names[0]] == decisions[names[1]]); checks += 4
        for observer in ('controller', 'collector'):
            bounds = [cases[n][observer + '_loss'] for n in names]
            same(r[observer + '_bounds'], bounds)
            same(r[observer + '_midpoint_range'], abs(sum(bounds[0]) - sum(bounds[1])) / 2)
            same(r[observer + '_common_overlap'], max(b[0] for b in bounds) <= min(b[1] for b in bounds)); checks += 3
    points = data['condition_means']
    same(Counter((r['condition_id'], r['method']) for r in points), Counter({(r['condition_id'], r['method']) for r in summary})); checks += 1
    for p in points:
        members = [r for r in summary if (r['condition_id'], r['method']) == (p['condition_id'], p['method'])]
        same((p['business_count'], p['deployments']), (4, 8)); same(len(members), 8)
        same(p['within'], sum(r['window_feasibility'] == 'within' for r in members))
        same(p['mean_coverage'], sum(r['weighted_active_interval_coverage'] for r in members) / 8)
        same(p['mean_fee_rates'], {k: average([r['fee_rates'][k] for r in members]) for k in members[0]['fee_rates']}); checks += 5
        for observer in ('controller', 'collector'):
            same(p[observer + '_bounds'], average([r[observer + '_loss'] for r in members])); checks += 1
    return checks


def check(output):
    summary, plan = load(output / 'summary.json'), load(output / 'stage-plan.json')
    data = {name: load(output / (name.replace('_', '-') + '.json')) for name in
            ('paired_cases', 'business_pairs', 'paired_groups', 'deployment_repeats', 'condition_means')}
    signatures, decisions, workloads = {}, {}, {}
    checks = 0
    for r in summary:
        name = r['batch_id'] + '/' + r['case']; directory = output / name
        packets = load(directory / 'packets.json'); ps = {p['packet_id']: p for p in packets}
        common = (sorted([business_packet(p) for p in packets], key=lambda p: (p['offset_ns'], p['flow'])),
                  load(directory / 'tasks.json'), load(directory / 'notice-schedule.json'))
        if r['business_seed'] in workloads:
            same(common, workloads[r['business_seed']], '跨条件同业务实际输入'); checks += 1
        workloads[r['business_seed']] = common
        signatures[name] = sorted([ps[x['packet_id']]['flow'], ps[x['packet_id']]['offset_ns'], z['switch_id']]
            for x in rows(directory / 'collector.jsonl') if x.get('ether_type') == 0x88b6 for z in x['records'])
        decisions[name] = [dict(offset=x['scheduled_offset_us'], skipped=x['skipped'], config=x.get('selected', {}).get('config'),
            writes=[{k: w.get(k) for k in ('node', 'flow', 'field', 'value', 'success')} for w in x.get('writes', [])])
            for x in rows(directory / 'decisions.jsonl')]
    same(signatures, load(output / 'sample-signatures.json'), '实际采样签名'); same(decisions, load(output / 'decision-signatures.json'), '实际配置签名')
    return checks + 2 + check_records(summary, plan, data, signatures, decisions)
