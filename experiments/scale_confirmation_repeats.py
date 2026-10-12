"""按批次身份归档静态候选的同业务三块部署变化。"""
from check_observation_wire import load, rows
from check_rolling_reference import same


def summarize(output, summary):
    signatures, workloads = {}, {}
    for case in summary:
        directory = output / case['batch_id'] / case['case']
        packets = load(directory / 'packets.json')
        by_pid = {p['packet_id']: p for p in packets}
        business = [(p['flow'], p['offset_ns'], p['path']) for p in packets]
        key = case['network_id'], case['scenario'], case['business_seed']
        complete = business, load(directory / 'tasks.json')
        if key in workloads:
            same(complete, workloads[key], '跨候选和部署块的共同业务与任务')
        workloads[key] = complete
        sampled = sorted((by_pid[r['packet_id']]['flow'], by_pid[r['packet_id']]['offset_ns'], record['switch_id'])
            for r in rows(directory / 'collector.jsonl') if r.get('ether_type') == 0x88b6
            for record in r['records'])
        signatures[case['batch_id'] + '/' + case['case']] = sampled
    groups = []
    keys = sorted({(c['network_id'], c['scenario'], c['candidate_id'], c['business_seed']) for c in summary})
    for network, scenario, candidate, seed in keys:
        cases = sorted([c for c in summary if (c['network_id'], c['scenario'], c['candidate_id'], c['business_seed']) ==
                       (network, scenario, candidate, seed)], key=lambda c: c['block'])
        same([c['block'] for c in cases], [0, 1, 2], '同业务三块完整分母')
        names = [c['batch_id'] + '/' + c['case'] for c in cases]
        row = dict(network_id=network, scenario=scenario, candidate_id=candidate, business_seed=seed, cases=names,
            same_sample_positions=all(signatures[n] == signatures[names[0]] for n in names),
            sample_counts=[len(signatures[n]) for n in names],
            feasibility=[c['window_feasibility'] for c in cases])
        for place in ('collector', 'controller'):
            bounds = [c[place + '_loss'] for c in cases]
            mids = [(a + b) / 2 for a, b in bounds]
            row[place + '_bounds'] = bounds
            row[place + '_common_overlap'] = max(a for a, b in bounds) <= min(b for a, b in bounds)
            row[place + '_midpoint_range'] = max(mids) - min(mids)
        groups.append(row)
    same(len(groups), 40, '全部配置与业务重复组')
    totals = dict(groups=40, same_positions_groups=sum(g['same_sample_positions'] for g in groups),
        collector_overlap_groups=sum(g['collector_common_overlap'] for g in groups),
        controller_overlap_groups=sum(g['controller_common_overlap'] for g in groups),
        max_controller_midpoint_range=max(g['controller_midpoint_range'] for g in groups))
    return groups, totals
