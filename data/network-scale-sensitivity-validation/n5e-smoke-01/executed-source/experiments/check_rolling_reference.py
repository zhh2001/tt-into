"""不调用策略或事件引擎，核对选择、门控、观察前缀、线格式及费用。"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path
import struct


def load(path):
    if path.suffix == '.gz':
        with gzip.open(path, 'rt') as stream:
            return json.load(stream)
    return json.loads(path.read_text())


def flow(f):
    return f['session'], f['tag']


def obj(o):
    return o['node'], o['port'], o['queue']


def label(f):
    return f'{f[0]}:{f[1]}'


def near(a, b, name):
    if not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-10):
        raise ValueError(name + ' 数值不一致')


def same(a, b, name):
    if a != b:
        raise ValueError(name + ' 不一致')


def violation(cost, budget):
    seconds = (cost['window_us'][1] - cost['window_us'][0]) / 1e6
    pairs = [(cost['inband_per_link_bytes_per_s'].get(k, 0), v) for k, v in budget['link_bytes_per_s'].items()]
    pairs += [(cost['export_attempt_bytes_per_s'], budget['export_bytes_per_s']),
              (sum(cost['control_serialized_bytes_by_kind'].values()) / seconds, budget['control_bytes_per_s'])]
    return sum(max(0, value / cap - 1) if cap else value for value, cap in pairs)


def clipped_loss(tasks, receipts, clip):
    # 从接收事件及截断转折建立分段，端点梯形积分；不复用被测解析公式。
    numerator, denominator = 0.0, 0.0
    for task in tasks:
        start, end, target = task['start_us'], task['end_us'], task['target_us']
        samples = sorted((r['received_us'], r['generated_us']) for r in receipts if obj(r['object_id']) == obj(task['object_id']))
        arrivals = defaultdict(list)
        for at, generated in samples:
            arrivals[at].append(generated)
        boundaries = sorted({start, end} | {at for at in arrivals if start < at < end})
        latest = max((g for at, g in samples if at <= start), default=None)
        area = 0.0
        for left, right in zip(boundaries, boundaries[1:]):
            if left in arrivals:
                latest = max(arrivals[left] + ([] if latest is None else [latest]))
            if latest is None:
                area += clip * (right - left)
            else:
                split = latest + clip * target
                points = [left] + ([split] if left < split < right else []) + [right]
                for a, b in zip(points, points[1:]):
                    area += (min(clip, (a - latest) / target) + min(clip, (b - latest) / target)) / 2 * (b - a)
        numerator += task['weight'] * area
        denominator += task['weight'] * (end - start)
    return numerator / denominator, denominator


def check_raw(workload, raw, budget, trained, manifest, fees):
    settings = manifest['settings']
    routes = {flow(r['flow']): r['objects'] for r in manifest['topology']['routes']}
    nodes = {n['node']: n for n in manifest['topology']['nodes']}
    keys = [(o['node'], f) for f in sorted(routes) for o in routes[f]]
    packets = workload['packets']
    by_id = {p['packet_id']: p for p in packets}
    initial = []
    for i, (key, theta) in enumerate(zip(keys, trained)):
        for field, value in [('threshold_us', settings['grid_us'][0] if theta is None else theta),
                             ('enabled', theta is not None)]:
            initial.append({'write_id': f'install-{i}-{field}', 'node': key[0],
                            'flow': {'session': key[1][0], 'tag': key[1][1]}, 'field': field, 'value': value,
                            'version': 1, 'issued_us': -1000, 'effective_us': -999, 'ack_us': -998,
                            'success': True, 'kind': 'installation', 'cost': fees[field]})
    writes = initial + [w for d in raw['decisions'] for w in d['writes']]
    reads = [r for d in raw['decisions'] for r in d['reads']]
    for d in raw['decisions']:
        now = d['time_us']
        same(d['view']['now_us'], now, '观察时刻')
        if len(d['writes']) > settings['max_update_rpcs']:
            raise ValueError('更新限额超出')
        if d['selected']['evaluated_candidates'] > settings['max_candidates']:
            raise ValueError('搜索限额超出')
        for operation in d['writes'] + d['reads']:
            if not now < operation['issued_us'] < now + settings['epoch_us']:
                raise ValueError('调用被回填或越过周期')
        expected_notices = [{'received_us': n['available_us'], 'version': n['version'], 'demands': n['demands']}
                            for n in workload['notices'] if n['available_us'] <= now]
        same(d['view']['notifications'], expected_notices, '需求可见前缀')
        expected_ack = [{'write_id': w['write_id'], 'received_us': w['ack_us'], 'node': w['node'],
                         'flow': w['flow'], 'field': w['field'], 'value': w['value'], 'version': w['version'],
                         'success': w['success']} for w in sorted(writes, key=lambda w: (w['ack_us'], w['issued_us'], w['write_id']))
                        if w['ack_us'] <= now]
        # 同时安装的确认顺序无语义差异，逐条内容必须一致。
        same(sorted(d['view']['confirmations'], key=lambda x: x['write_id']),
             sorted(expected_ack, key=lambda x: x['write_id']), '写确认前缀')
        count_rows = []
        for r in sorted(reads, key=lambda r: r['available_us']):
            if r['available_us'] <= now and r['success']:
                count = sum(flow(p['flow']) == flow(r['flow']) and p['hops'][0]['ingress_us'] < r['snapshot_us'] for p in packets)
                count_rows.append({'read_id': r['read_id'], 'flow': r['flow'], 'issued_us': r['issued_us'],
                                   'available_us': r['available_us'], 'count': count})
        same(d['view']['counts'], count_rows, '付费计数前缀')
        fields = {key: {'enabled': False, 'threshold_us': 100000} for key in keys}
        for w in sorted(writes, key=lambda w: (w['version'], w['ack_us'])):
            if w['ack_us'] <= now and w['success']:
                fields[w['node'], flow(w['flow'])][w['field']] = w['value']
        current = [fields[k]['threshold_us'] if fields[k]['enabled'] else None for k in keys]
        same(d['current_config'], current, '确认配置')
        planned = []
        for key, theta in zip(keys, d['selected']['config']):
            old = fields[key]
            if theta is None:
                if old['enabled']:
                    planned.append((key, 'enabled', False))
            else:
                if old['threshold_us'] != theta:
                    planned.append((key, 'threshold_us', theta))
                if not old['enabled']:
                    planned.append((key, 'enabled', True))
        actual = [((w['node'], flow(w['flow'])), w['field'], w['value']) for w in d['writes']]
        same(actual, planned, '阈值与许可过渡计划')
        for w in d['writes']:
            expected = fees[w['field']]
            same((w['cost']['request_bytes'], w['cost']['reply_bytes']),
                 (expected['request_bytes'], expected['reply_bytes']), '写 API 费用')
        for r in d['reads']:
            expected = fees['read']
            same((r['cost']['request_bytes'], r['cost']['reply_bytes']),
                 (expected['request_bytes'], expected['reply_bytes']), '读 API 费用')

    # 使用输入 egress 顺序和实际写生效时间独立重建门控，不使用引擎的 egress 日志。
    schedule = [(w['effective_us'], 0, i, 'write', w) for i, w in enumerate(writes)]
    schedule += [(h['egress_us'], 1, p['packet_id'] * 256 + i, 'hop', (p, h))
                 for p in packets for i, h in enumerate(p['hops'])]
    fields = {k: {'enabled': False, 'threshold_us': 100000} for k in keys}
    gates, selected = {}, {str(p['packet_id']): [] for p in packets}
    for _, _, _, kind, payload in sorted(schedule):
        if kind == 'write':
            w = payload
            if w['success']:
                fields[w['node'], flow(w['flow'])][w['field']] = w['value']
        else:
            p, h = payload
            node, f = h['object_id']['node'], flow(p['flow'])
            key = node, f
            policy, chosen = fields[key], selected[str(p['packet_id'])]
            clock = nodes[node]
            local = (h['ingress_us'] + clock['offset_us']) % (1 << clock['clock_bits'])
            eligible = key not in gates or (local - gates[key]) % (1 << clock['clock_bits']) >= policy['threshold_us']
            if policy['enabled'] and len(chosen) < settings['capacity'] and eligible:
                chosen.append(node)
                gates[key] = local
    result = raw['result']
    same(result['selected'], selected, '独立门控与容量')
    payloads, ledger = {}, []
    for p in packets:
        chosen = selected[str(p['packet_id'])]
        for i, h in enumerate(p['hops']):
            prefix = sum(previous['object_id']['node'] in chosen for previous in p['hops'][:i + 1])
            ledger.append((h['egress_us'], 'inband', h['link'], 12 + 12 * prefix))
        if chosen:
            size = max(60, 46 + 12 * len(chosen))
            ledger.append((p['sink_us'], 'export', None, size))
            encoded = struct.pack('!BBBBII', len(chosen), 255, 6, 1, p['flow']['tag'], p['flow']['session'])
            records = []
            for node in chosen:
                index = next(i for i, h in enumerate(p['hops']) if h['object_id']['node'] == node)
                h, clock = p['hops'][index], nodes[node]
                local = (h['egress_us'] + clock['offset_us']) % (1 << clock['clock_bits'])
                encoded += bytes((node, index + 1)) + local.to_bytes(6, 'big') + h['queue_packets'].to_bytes(4, 'big')
                records.append({'node': node, 'hop': index + 1, 'generated_local_us': local, 'queue_packets': h['queue_packets']})
            payloads[p['packet_id']] = encoded.hex(), records
    same({e['packet_id']: e['payload_hex'] for e in result['exports']}, {k: v[0] for k, v in payloads.items()}, '独立线格式')
    reports = sorted((at, p['packet_id']) for p in packets if p['packet_id'] in payloads for at in p['report_arrivals_us'])
    receipts, cache = [], {}
    for at, pid in reports:
        p = by_id[pid]
        for record in payloads[pid][1]:
            h = p['hops'][record['hop'] - 1]
            key, generated = obj(h['object_id']), h['egress_us']
            accepted = key not in cache or generated > cache[key]
            if accepted:
                cache[key] = generated
            receipts.append({'object_id': h['object_id'], 'generated_us': generated, 'received_us': at, 'accepted': accepted})
    same(result['receipts'], receipts, '生成真值与有效缓存前进')
    for d in raw['decisions']:
        now = d['time_us']
        expected_reports = [{'received_us': at, 'flow': by_id[pid]['flow'], 'records': payloads[pid][1]}
                            for at, pid in reports if at <= now]
        same(d['view']['reports'], expected_reports, '实收报告前缀')
        expected_cache = {}
        for r in receipts:
            if r['received_us'] <= now and r['accepted']:
                o = r['object_id']
                h = next(h for p in packets for h in p['hops'] if h['object_id'] == o and h['egress_us'] == r['generated_us'])
                expected_cache[obj(o)] = {'object_id': o, 'generated_local_us': nodes[o['node']]['offset_us'] + r['generated_us'],
                                          'received_us': r['received_us'], 'queue_packets': h['queue_packets']}
        for c in expected_cache.values():
            c['generated_local_us'] %= 1 << nodes[c['object_id']['node']]['clock_bits']
        same(d['view']['cache'], [expected_cache[k] for k in sorted(expected_cache)], '合法缓存前缀')
    for w in writes:
        ledger += [(w['issued_us'], 'control', w['kind'], w['cost']['request_bytes']),
                   (w['ack_us'], 'control', w['kind'], w['cost']['reply_bytes'])]
    for r in reads:
        ledger += [(r['issued_us'], 'control', 'observation', r['cost']['request_bytes']),
                   (r['available_us'], 'control', 'observation', r['cost']['reply_bytes'])]
    for n in workload['notices']:
        ledger += [(n['available_us'], 'control', 'observation', n['cost']['request_bytes']),
                   (n['available_us'], 'control', 'observation', n['cost']['reply_bytes'])]
    normalized_actual = Counter((e['time_us'], e['resource'], e.get('link') if e['resource'] == 'inband' else e.get('kind'),
                                 e['bytes']) for e in result['cost_events'] if e['bytes'])
    normalized_expected = Counter(e for e in ledger if e[3])
    same(normalized_actual, normalized_expected, '逐项独立费用账本')
    for cost in [raw['summary']['cost']] + raw['epoch_costs']:
        left, right = cost['window_us']
        seconds = (right - left) / 1e6
        within = [e for e in ledger if left <= e[0] < right]
        links = {k: sum(e[3] for e in within if e[1] == 'inband' and e[2] == k) for k in budget['link_bytes_per_s']}
        controls = {k: sum(e[3] for e in within if e[1] == 'control' and e[2] == k) for k in ('installation', 'observation', 'update')}
        exported = sum(e[3] for e in within if e[1] == 'export')
        same(cost['inband_per_link_bytes'], links, '窗口带内费用')
        same(cost['control_serialized_bytes_by_kind'], controls, '窗口控制费用')
        same(cost['export_attempt_bytes'], exported, '窗口导出尝试费用')
        for k in links:
            near(cost['inband_per_link_bytes_per_s'][k], links[k] / seconds, '带内费用率')
        near(cost['export_attempt_bytes_per_s'], exported / seconds, '导出费用率')
    expected_loss, denominator = clipped_loss(workload['tasks'], receipts, settings['clip'])
    near(raw['summary']['loss'], expected_loss, '分段梯形损失')
    near(raw['task_metrics']['weighted_active_duration_us'], denominator, '完整任务分母')
    expected_excess = violation(raw['summary']['cost'], budget)
    near(raw['summary']['excess'], expected_excess, '实际预算超限')
    same(raw['summary']['feasible'], expected_excess <= 1e-12, '实际预算标签')
    same(raw['summary']['over_budget_epoch_count'], sum(violation(c, budget) > 1e-12 for c in raw['epoch_costs']), '周期违约数')
    return {'packets': len(packets), 'decisions': len(raw['decisions']), 'ledger_entries': len(ledger)}


def audit(run):
    manifest = load(run / 'manifest.json')
    same(manifest['status'], 'passed', '运行状态')
    for name, digest in manifest['artifact_sha256'].items():
        same(hashlib.sha256((run / name).read_bytes()).hexdigest(), digest, '产物摘要 ' + name)
    for name, digest in manifest['source_sha256'].items():
        same(hashlib.sha256((run / 'executed-source' / name).read_bytes()).hexdigest(), digest, '源码摘要 ' + name)
    if set(manifest['train_seeds']) & set(manifest['test_seeds']):
        raise ValueError('训练与测试种子相交')
    all_configs = set(itertools.product([None] + manifest['settings']['grid_us'], repeat=4))
    groups = defaultdict(list)
    for line in (run / 'training.jsonl').read_text().splitlines():
        row = json.loads(line)
        groups[row['scenario'], row['budget_level']].append(row)
        near(row['excess'], violation(row['cost'], manifest['budget_definition'][row['scenario']][row['budget_level']]), '训练超限计算')
    selections = load(run / 'training-selections.json')
    expected_groups = set(itertools.product(manifest['scenarios'], manifest['budgets']))
    same(set(groups), expected_groups, '训练场景与预算全集')
    same(Counter((s['scenario'], s['budget_level']) for s in selections), Counter(expected_groups), '训练选择全集')
    for selected in selections:
        rows = groups[selected['scenario'], selected['budget_level']]
        actual = Counter((r['seed'], tuple(r['config'])) for r in rows)
        expected = Counter((seed, config) for seed in manifest['train_seeds'] for config in all_configs)
        same(actual, expected, '完整训练网格')
        scores = []
        for config in all_configs:
            relevant = [r for r in rows if tuple(r['config']) == config]
            score = (max(r['excess'] for r in relevant), sum(r['loss'] for r in relevant) / len(relevant),
                     sum(v is not None for v in config), tuple(-1 if v is None else v for v in config))
            scores.append((score, config))
        same(tuple(selected['config']), min(scores)[1], '训练选择不含测试')
    rows = load(run / 'summary.json')
    expected_runs = set(itertools.product(manifest['scenarios'], manifest['budgets'], manifest['test_seeds'], manifest['methods']))
    same(Counter((r['scenario'], r['budget_level'], r['seed'], r['method']) for r in rows), Counter(expected_runs), '完整配对运行集')
    totals = Counter()
    fees = load(run / 'rpc-calibration.json')['calls']
    for row in rows:
        scenario, level, seed, method = row['scenario'], row['budget_level'], row['seed'], row['method']
        workload = load(run / 'workloads' / f'{scenario}-{seed}.json.gz')
        raw = load(run / 'runs' / f'{scenario}-{level}-{seed}-{method}.json.gz')
        same(raw['summary'], row, '原始与汇总结果')
        trained = next(r['config'] for r in selections if r['scenario'] == scenario and r['budget_level'] == level)
        counts = check_raw(workload, raw, manifest['budget_definition'][scenario][level], trained, manifest, fees)
        totals.update(counts)
    return {'status': 'passed', 'heldout_runs': len(rows), 'training_runs': sum(len(v) for v in groups.values()),
            **dict(totals), 'scope': '费用、门控、线格式、观察前缀、积分与训练选择的独立核对，不是一般调度证明'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.run), ensure_ascii=False, indent=2))
