"""分开重建方法效应和相对中心条件效应，保留四业务与两部署分母。"""
from collections import Counter
from check_observation_wire import load, rows
from check_rolling_reference import same
from diagnose_observer_training import business_packet

KEYS = ('condition_id', 'business_seed', 'block', 'method')
OBSERVERS = ('controller', 'collector')


def mean(values):
    return [None if any(v[i] is None for v in values) else sum(v[i] for v in values) / len(values) for i in (0, 1)]


def complete(summary, plan):
    actual = Counter(tuple(r[k] for k in KEYS) for r in summary)
    same(actual, Counter(tuple(e[k] for k in KEYS) for e in plan['execution_order']), '五条件完整业务与部署身份')
    if set(actual.values()) != {1}:
        raise ValueError('敏感性身份重复')


def records(summary, signatures, decisions, plan):
    complete(summary, plan)
    indexed = {tuple(r[k] for k in KEYS): r for r in summary}
    links = []
    for key, left in sorted(indexed.items()):
        condition, seed, block, method = key
        if method == 'phase_guard':
            links.append(('method', left, indexed[condition, seed, block, 'static']))
        if condition != 'B100':
            links.append(('condition', left, indexed['B100', seed, block, method]))
    pairs = []
    for relation, left, right in links:
        a, b = (r['batch_id'] + '/' + r['case'] for r in (left, right))
        row = {k: left[k] for k in KEYS}
        row.update(relation=relation, left_case=a, right_case=b,
            left_feasibility=left['window_feasibility'], right_feasibility=right['window_feasibility'],
            both_within=left['window_feasibility'] == right['window_feasibility'] == 'within',
            same_sample_positions=signatures[a] == signatures[b],
            left_fee_rates=left['fee_rates'], right_fee_rates=right['fee_rates'],
            left_coverage=left['weighted_active_interval_coverage'], right_coverage=right['weighted_active_interval_coverage'])
        for observer in OBSERVERS:
            x, z = left[observer + '_loss'], right[observer + '_loss']
            delta = [x[0] - z[1], x[1] - z[0]]
            row[observer + '_left_bounds'], row[observer + '_right_bounds'] = x, z
            row[observer + '_difference_bounds'] = delta
            row[observer + '_direction'] = 'lower' if delta[1] < 0 else 'higher' if delta[0] > 0 else 'overlap'
            row[observer + '_midpoint_relative_reduction'] = (sum(z) - sum(x)) / sum(z) if sum(z) > 0 else None
        pairs.append(row)

    def aggregate(values, identity, business_count):
        row = dict(identity, business_count=business_count, deployment_pairs=len(values),
            both_within_pairs=sum(p['both_within'] for p in values),
            same_sample_position_pairs=sum(p['same_sample_positions'] for p in values))
        for side in ('left', 'right'):
            row[side + '_mean_coverage'] = sum(p[side + '_coverage'] for p in values) / len(values)
            row[side + '_mean_fee_rates'] = {k: mean([p[side + '_fee_rates'][k] for p in values])
                                           for k in values[0][side + '_fee_rates']}
        for observer in OBSERVERS:
            for kind in ('left', 'right', 'difference'):
                row[observer + '_mean_' + kind + '_bounds'] = mean([p[observer + '_' + kind + '_bounds'] for p in values])
            x, z = row[observer + '_mean_left_bounds'], row[observer + '_mean_right_bounds']
            row[observer + '_midpoint_relative_reduction'] = (sum(z) - sum(x)) / sum(z) if sum(z) > 0 else None
            row[observer + '_directions'] = dict(Counter(p[observer + '_direction'] for p in values))
        return row

    businesses, groups, repeats, points = [], [], [], []
    for key in sorted({(p['relation'], p['condition_id'], p['method'], p['business_seed']) for p in pairs}):
        values = [p for p in pairs if (p['relation'], p['condition_id'], p['method'], p['business_seed']) == key]
        same(len(values), 2, '同业务两部署方法或条件配对')
        businesses.append(aggregate(values, dict(zip(('relation', 'condition_id', 'method', 'business_seed'), key)), 1))
    for key in sorted({(p['relation'], p['condition_id'], p['method']) for p in pairs}):
        values = [p for p in pairs if (p['relation'], p['condition_id'], p['method']) == key]
        same(sorted({p['business_seed'] for p in values}), sorted(plan['business_seeds']), '四独立业务分母')
        groups.append(aggregate(values, dict(zip(('relation', 'condition_id', 'method'), key)), 4))
    for key in sorted({(r['condition_id'], r['business_seed'], r['method']) for r in summary}):
        condition, seed, method = key
        values = [indexed[condition, seed, block, method] for block in plan['blocks']]
        names = [r['batch_id'] + '/' + r['case'] for r in values]
        row = dict(zip(('condition_id', 'business_seed', 'method'), key))
        row.update(cases=names, same_sample_positions=signatures[names[0]] == signatures[names[1]],
                   same_decision_sequence=decisions[names[0]] == decisions[names[1]],
                   feasibility=[r['window_feasibility'] for r in values])
        for observer in OBSERVERS:
            bounds = [r[observer + '_loss'] for r in values]
            row[observer + '_bounds'] = bounds
            row[observer + '_midpoint_range'] = abs(sum(bounds[0]) - sum(bounds[1])) / 2
            row[observer + '_common_overlap'] = max(x[0] for x in bounds) <= min(x[1] for x in bounds)
        repeats.append(row)
    for key in sorted({(r['condition_id'], r['method']) for r in summary}):
        values = [r for r in summary if (r['condition_id'], r['method']) == key]
        points.append(dict(condition_id=key[0], method=key[1], business_count=4, deployments=8,
            within=sum(r['window_feasibility'] == 'within' for r in values),
            collector_bounds=mean([r['collector_loss'] for r in values]), controller_bounds=mean([r['controller_loss'] for r in values]),
            mean_coverage=sum(r['weighted_active_interval_coverage'] for r in values) / 8,
            mean_fee_rates={k: mean([r['fee_rates'][k] for r in values]) for k in values[0]['fee_rates']}))
    same((len(pairs), len(businesses), len(groups), len(repeats), len(points)), (104, 52, 13, 40, 10), '全部敏感性配对分母')
    return dict(paired_cases=pairs, business_pairs=businesses, paired_groups=groups, deployment_repeats=repeats, condition_means=points)


def summarize(output, summary, plan):
    signatures, decisions, workloads = {}, {}, {}
    for row in summary:
        name = row['batch_id'] + '/' + row['case']
        directory = output / name
        packets = load(directory / 'packets.json')
        normalized = sorted([business_packet(p) for p in packets], key=lambda p: (p['offset_ns'], p['flow']))
        seed = row['business_seed']
        common = normalized, load(directory / 'tasks.json'), load(directory / 'notice-schedule.json')
        if seed in workloads:
            same(common, workloads[seed], '同业务全部条件、方法和两部署提供及任务相同')
        workloads[seed] = common
        ps = {p['packet_id']: p for p in packets}
        signatures[name] = sorted([ps[r['packet_id']]['flow'], ps[r['packet_id']]['offset_ns'], x['switch_id']]
            for r in rows(directory / 'collector.jsonl') if r.get('ether_type') == 0x88b6 for x in r['records'])
        decisions[name] = [dict(offset=r['scheduled_offset_us'], skipped=r['skipped'], config=r.get('selected', {}).get('config'),
            writes=[{k: w.get(k) for k in ('node', 'flow', 'field', 'value', 'success')} for w in r.get('writes', [])])
            for r in rows(directory / 'decisions.jsonl')]
    return dict(sample_signatures=signatures, decision_signatures=decisions, **records(summary, signatures, decisions, plan))
