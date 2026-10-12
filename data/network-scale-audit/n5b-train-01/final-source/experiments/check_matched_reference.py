"""独立核对分层事件、合法费用前缀、训练集合与参数冻结。"""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import copy
import gzip
import hashlib
import itertools
import json
from pathlib import Path

from check_rolling_reference import check_raw, clipped_loss, flow, load, near, same, violation
from check_adaptive_reference import check_adaptive_raw


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def local_violation(cost, cap):
    rate = cost['local_notice_payload_bytes_per_s']
    return max(0, rate / cap - 1) if cap else rate


def prefix_window(charges, now, start=2000000000, end=10000000000):
    end = min(end, max(start, now + 1))
    totals = {'api_serialized': 0, 'local_notice_payload': 0}
    kinds = {k: {} for k in totals}
    for r in charges:
        if r['known_ns'] <= now and start <= r['time_ns'] < end:
            totals[r['layer']] += r['bytes']
            kinds[r['layer']][r['kind']] = kinds[r['layer']].get(r['kind'], 0) + r['bytes']
    return {'window_ns': [start, end], 'bytes_by_layer': totals, 'bytes_by_layer_and_kind': kinds}


def check_layered(workload, raw, budget, trained, manifest, fees, parameters):
    # 旧检查器继续完整核对包/报告/门控/API；通知在新层单独重建，不伪作 RPC。
    native = raw
    adapted_w = copy.deepcopy(workload)
    for n in adapted_w['notices']:
        n['cost'] = {'request_bytes': 0, 'reply_bytes': 0, 'provenance': '本地消息独立层，不属于 Thrift'}
    adapted = copy.deepcopy(raw)
    adapted['result']['cost_events'] = [e for e in adapted['result']['cost_events'] if e['resource'] != 'local_notice']
    adapted['summary']['excess'] = violation(adapted['summary']['cost'], budget)
    adapted['summary']['feasible'] = adapted['summary']['excess'] <= 1e-12
    adapted['summary']['over_budget_epoch_count'] = sum(violation(c, budget) > 1e-12 for c in adapted['epoch_costs'])
    totals = check_raw(adapted_w, adapted, budget, trained, manifest, fees)
    checks = 0
    notices = []
    for n in workload['notices']:
        message = {'version': n['version'], 'demands': [{'node': d['object_id']['node'], 'target_us': d['target_us'],
                   'weight': d['weight'], 'active': d['active']} for d in n['demands']]}
        body = json.dumps(message, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
        same(n['payload'], body.hex(), '实际接口消息编码')
        same(n['available_us'], ((n['released_us'] + 1 + manifest['settings']['epoch_us'] - 1) // manifest['settings']['epoch_us']) * manifest['settings']['epoch_us'],
             '消息释放及轮询可用合同')
        notices.append({'time_us': n['available_us'], 'resource': 'local_notice', 'kind': 'management_notice',
                        'operation_id': n['notice_id'], 'part': 'payload', 'bytes': len(body)})
    actual = [{k: e[k] for k in notices[0]} for e in raw['result']['cost_events'] if e['resource'] == 'local_notice']
    same(actual, notices, '本地消息逐项费用')
    expected_calls = []
    keys = [(o['node'], r['flow']) for r in manifest['topology']['routes'] for o in r['objects']]
    writes = [dict(write_id=f'install-{i}-{field}', kind='installation', issued_us=-1000, ack_us=-998, cost=fees[field], success=True)
              for i in range(len(keys)) for field in ('threshold_us', 'enabled')]
    writes += [w for d in raw['decisions'] for w in d['writes']]
    reads = [r for d in raw['decisions'] for r in d['reads']]
    for ops, is_write in ((writes, True), (reads, False)):
        for op in ops:
            expected_calls.append({'operation_id': op['write_id'] if is_write else op['read_id'],
                'kind': op['kind'] if is_write else 'observation', 'before_monotonic_ns': op['issued_us'] * 1000,
                'after_monotonic_ns': (op['ack_us'] if is_write else op['available_us']) * 1000,
                'request_serialized_bytes': op['cost']['request_bytes'], 'reply_serialized_bytes': op['cost']['reply_bytes'], 'success': op['success']})
    same(sorted(raw['result']['completed_calls'], key=lambda r: r['operation_id']),
         sorted(expected_calls, key=lambda r: r['operation_id']), '自有合成调用完成来源')
    charges = []
    for call in expected_calls:
        for part, time, key in (('request', 'before_monotonic_ns', 'request_serialized_bytes'), ('reply', 'after_monotonic_ns', 'reply_serialized_bytes')):
            charges.append({'operation_id': call['operation_id'], 'layer': 'api_serialized', 'kind': call['kind'], 'part': part,
                            'time_ns': call[time], 'known_ns': call['after_monotonic_ns'], 'bytes': call[key], 'success': call['success']})
    for n in notices:
        charges.append({'operation_id': n['operation_id'], 'layer': 'local_notice_payload', 'kind': n['kind'], 'part': 'payload',
                        'time_ns': n['time_us'] * 1000, 'known_ns': n['time_us'] * 1000, 'bytes': n['bytes'], 'success': True})
    configuration_set = set(itertools.product((None,) + tuple(manifest['settings']['grid_us']), repeat=4))
    for d in raw['decisions']:
        now = d['time_us'] * 1000
        same(d['control_snapshot']['cutoff_ns'], now, '控制前缀时刻')
        expected = sorted([r for r in charges if r['known_ns'] <= now], key=lambda r: (r['time_ns'], r['operation_id'], r['part']))
        observed = [{k: r[k] for k in charges[0]} for r in d['control_snapshot']['charges']]
        same(observed, expected, '自有已知费用前缀')
        same(d['budget_diagnostic']['control_ledger_window'], prefix_window(charges, now), '分层费用窗口前缀')
        spend = prefix_window(charges, now)['bytes_by_layer']
        near(d['budget_diagnostic']['spend']['control_bytes'], spend['api_serialized'], '实际合成 API 已知费用')
        near(d['budget_diagnostic']['spend']['notice_payload_bytes'], spend['local_notice_payload'], '实际消息已知费用')
        seconds = d['budget_diagnostic']['remaining_s']
        reserve = parameters['reserve_fraction']
        near(d['budget_diagnostic']['effective_budget']['control_bytes_per_s'],
             max(0, (8 * budget['control_bytes_per_s'] - spend['api_serialized']) / seconds) * (1 - reserve), '剩余 API 预算')
        for r in d['candidate_scores']:
            if tuple(r['config']) not in configuration_set:
                raise ValueError('候选不属于共同网格')
            near(r['predicted_excess'], violation(r['cost'], budget) + d['budget_diagnostic']['known_notice_debt'], '候选名义分层预算')
            near(r['effective_budget_excess'], violation(r['cost'], d['budget_diagnostic']['effective_budget']) + d['budget_diagnostic']['known_notice_debt'], '候选剩余分层预算')
            same(r['cost']['control_serialized_bytes_by_kind']['observation'], 2 * (fees['read']['request_bytes'] + fees['read']['reply_bytes']), '通知不重复预收费')
        checks += len(expected) + len(d['candidate_scores']) * 3 + 5
    for cost in [raw['summary']['cost']] + raw['epoch_costs']:
        a, b = cost['window_us']
        amount = sum(n['bytes'] for n in notices if a <= n['time_us'] < b)
        same(cost['local_notice_payload_bytes'], amount, '消息窗口费用')
        near(cost['local_notice_payload_bytes_per_s'], amount / ((b - a) / 1e6), '消息费用率')
    cap = manifest['notice_budget_bytes_per_s']
    excess = violation(raw['summary']['cost'], budget) + local_violation(raw['summary']['cost'], cap)
    near(raw['summary']['excess'], excess, '完整分层超限')
    same(raw['summary']['feasible'], excess <= 1e-12, '完整分层达标')
    same(raw['summary']['over_budget_epoch_count'], sum(violation(c, budget) + local_violation(c, cap) > 1e-12 for c in raw['epoch_costs']), '完整分层秒级超限')
    if raw['summary']['method'] not in ('static', 'feedback', 'local', 'joint'):
        source_copy = copy.deepcopy(raw)
        # 数据/相位历史仍由旧独立检查核对，新控制费用在上面直接重建。
        for d in source_copy['decisions']:
            legacy = d['budget_diagnostic']['spend']['unused_legacy_control_prediction_bytes']
            d['budget_diagnostic']['spend']['control_bytes'] = legacy
            d['budget_diagnostic']['effective_budget']['control_bytes_per_s'] = max(0, (8 * budget['control_bytes_per_s'] - legacy) / d['budget_diagnostic']['remaining_s']) * (1 - reserve)
        empty = [d for d in source_copy['decisions'] if not d['view']['notifications']]
        for d in empty:
            same(d['selected']['config'], d['current_config'], '通知尚不可用时保持配置')
        source_copy['decisions'] = [d for d in source_copy['decisions'] if d['view']['notifications']]
        checks += check_adaptive_raw(source_copy, manifest, parameters)
    return dict(totals, control_checks=checks)


def training_check(job):
    run, scenario, seed, manifest = job
    run = Path(run)
    w = load(run / 'training-workloads' / f'{scenario}-{seed}.json.gz')
    packets = sorted(w['packets'], key=lambda p: (p['hops'][0]['ingress_us'], p['packet_id']))
    if any(p['ip_bytes'] != 120 or p['hops'][-1]['egress_us'] >= q['hops'][0]['ingress_us'] for p, q in zip(packets, packets[1:])):
        raise ValueError('训练快捷独立核对需要无跨包出队交错的已声明输入')
    keys = [(o['node'], flow(r['flow'])) for r in manifest['topology']['routes'] for o in r['objects']]
    expected_set = set(itertools.product((None,) + tuple(manifest['settings']['grid_us']), repeat=4))
    seen = set()
    with gzip.open(run / 'training' / f'{scenario}-{seed}.jsonl.gz', 'rt') as stream:
        for line in stream:
            row = json.loads(line)
            config = tuple(row['config'])
            if config in seen or config not in expected_set:
                raise ValueError('训练集合重复或网格错误')
            seen.add(config)
            gates, receipts, events = {}, [], []
            policy = dict(zip(keys, config))
            for p in packets:
                chosen = []
                for h in p['hops']:
                    key = h['object_id']['node'], flow(p['flow'])
                    threshold = policy[key]
                    if threshold is not None and not chosen and (key not in gates or h['ingress_us'] - gates[key] >= threshold):
                        gates[key] = h['ingress_us']
                        chosen.append(h)
                    events.append((h['egress_us'], 'inband', h['link'], 12 + 12 * len(chosen)))
                if chosen:
                    events.append((p['sink_us'], 'export', '', 60))
                    for at in p['report_arrivals_us']:
                        receipts.append({'object_id': chosen[0]['object_id'], 'generated_us': chosen[0]['egress_us'], 'received_us': at})
            cost = row['cost']
            within = [e for e in events if 2000000 <= e[0] < 10000000]
            for link in ('e1', 'e2', 'e3'):
                amount = sum(e[3] for e in within if e[1] == 'inband' and e[2] == link)
                same(cost['inband_per_link_bytes'][link], amount, '训练独立链路字节')
                near(cost['inband_per_link_bytes_per_s'][link], amount / 8, '训练独立链路费用率')
            exported = sum(e[3] for e in within if e[1] == 'export')
            same(cost['export_attempt_bytes'], exported, '训练独立尝试费用')
            near(cost['export_attempt_bytes_per_s'], exported / 8, '训练独立导出率')
            same(sum(cost['control_serialized_bytes_by_kind'].values()), 0, '静态训练无测量窗口轮询')
            amount = sum(len(bytes.fromhex(n['payload'])) for n in w['notices'] if 2000000 <= n['available_us'] < 10000000)
            same(cost['local_notice_payload_bytes'], amount, '训练消息独立费用')
            near(cost['local_notice_payload_bytes_per_s'], amount / 8, '训练独立消息率')
            near(row['loss'], clipped_loss(w['tasks'], receipts, 4)[0], '训练独立连续损失')
            for level in ('low', 'high'):
                near(row['budget_scores'][level], violation(cost, manifest['budget_definition'][scenario][level]) + local_violation(cost, manifest['notice_budget_bytes_per_s']), '两预算评分复用')
    same(seen, expected_set, '训练完整共同集合')
    return len(seen)


def check_solver(run, manifest, fees, frozen):
    from check_matched_preflight import rank
    entries = load(run / 'solver-diagnostics.json')
    expected = set(itertools.product(('stable', 'shared_priority', 'unannounced_switch'), ('low', 'high'), (4000000, 7000000)))
    same(Counter((d['scenario'], d['budget_level'], d['time_us']) for d in entries), Counter(expected), '预声明代理诊断集合')
    grid = set(itertools.product((None,) + tuple(manifest['settings']['grid_us']), repeat=4))
    keys = ((1, 20, 1001), (3, 20, 1001), (2, 20, 2002), (3, 20, 2002))
    checks = 0
    for d in entries:
        full = load(run / 'solver' / f'{d["scenario"]}-{d["budget_level"]}-{d["time_us"]}.json.gz')
        same(full['diagnostic'], d, '代理诊断摘要来源')
        same(d['parameter_index'], frozen['selected_indices']['phase_guard'], '代理诊断冻结参数')
        candidates = full['candidates']
        same(len(candidates), 6561, '代理诊断完整配置数')
        same({tuple(r['config']) for r in candidates}, grid, '代理诊断完整共同网格')
        raw = load(run / d['input_calibration_run'])
        view = next(r for r in raw['decisions'] if r['time_us'] == d['time_us'])['view']
        fields, versions = {}, {}
        for r in view['confirmations']:
            if not r['success']:
                continue
            key = (r['node'], r['flow']['session'], r['flow']['tag'], r['field'])
            stamp = (r['version'], r['received_us'])
            if stamp >= versions.get(key, (-1, -10**30)):
                fields[key], versions[key] = r['value'], stamp
        eligible = []
        for r in candidates:
            changed = []
            for key, val in zip(keys, r['config']):
                enabled = fields.get(key + ('enabled',), False)
                threshold = fields.get(key + ('threshold_us',), 100000)
                if val is None and enabled:
                    changed.append((key, 'enabled'))
                if val is not None:
                    if threshold != val:
                        changed.append((key, 'threshold_us'))
                    if not enabled:
                        changed.append((key, 'enabled'))
            same(r['update_rpc_count'], len(changed), '完整代理动作费用数')
            same(r['update_allowed'], len(changed) <= 6, '完整代理动作限额')
            same(r['cost']['control_serialized_bytes_by_kind'], {'installation': 0,
                 'observation': 2 * (fees['read']['request_bytes'] + fees['read']['reply_bytes']),
                 'update': sum(fees[f]['request_bytes'] + fees[f]['reply_bytes'] for _, f in changed)}, '完整代理控制费用')
            if r['update_allowed']:
                eligible.append(r)
        for method, local in (('joint', False), ('local', True)):
            best = min(eligible, key=lambda r: rank(r, local))
            limited = d[method]['limited']
            same(d[method]['exact'], best, '代理网格参照排序')
            if limited['evaluated_candidates'] > 48 or limited['forecast'] not in candidates:
                raise ValueError('代理有限搜索结果或限额错误')
            same(d[method]['rank_matches'], rank(best, local) == rank(limited['forecast'], local), '代理搜索排序差距')
            field = 'local_loss' if local else 'predicted_loss'
            near(d[method]['objective_gap'], limited['forecast'][field] - best[field], '代理搜索目标差距')
        checks += 3 * len(candidates) + 10
    return {'points': len(entries), 'candidates': len(entries) * 6561, 'checks': checks}


def audit(run, workers=6):
    m = load(run / 'manifest.json')
    same(m['status'], 'passed', '运行完成')
    for name, digest in m['source_sha256'].items():
        same(sha(run / 'executed-source' / name), digest, '执行源码摘要')
    for name, digest in m['artifact_sha256'].items():
        same(sha(run / name), digest, '产物摘要')
    selections = load(run / 'training-selections.json')
    fees = {k: {f: v[f] for f in ('request_bytes', 'reply_bytes', 'provenance')}
            for k, v in load(run / 'rpc-reference.json')['calls'].items() if k in ('read', 'enabled', 'threshold_us')}
    frozen = load(run / 'frozen-parameters.json')
    same(frozen['training_selection_sha256'], sha(run / 'training-selections.json'), '静态训练冻结')
    groups = [set(m[k]) for k in ('train_seeds', 'calibration_seeds', 'test_seeds')]
    if any(a & b for i, a in enumerate(groups) for b in groups[i + 1:]):
        raise ValueError('训练/校准/保留分组交叉')
    jobs = [(str(run), s, seed, m) for s, seed in itertools.product(m['scenarios'], m['train_seeds'])]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        training_count = sum(pool.map(training_check, jobs))
    same(training_count, m['static_replays'], '完整训练实际回放数')
    # 从两训练文件独立选最小字典序，原始费用与损失已逐配置核对。
    for s in m['scenarios']:
        seed_rows = []
        for seed in m['train_seeds']:
            with gzip.open(run / 'training' / f'{s}-{seed}.jsonl.gz', 'rt') as stream:
                seed_rows.append([json.loads(l) for l in stream])
        for level in m['budgets']:
            choices = []
            for rows in zip(*seed_rows):
                c = rows[0]['config']
                score = [max(r['budget_scores'][level] for r in rows), sum(r['loss'] for r in rows) / 2,
                         sum(v is not None for v in c), [-1 if v is None else v for v in c]]
                choices.append((score, c))
            best, c = min(choices)
            selected = next(r for r in selections if r['scenario'] == s and r['budget_level'] == level)
            same(selected['config'], c, '充分训练静态选择')
            same(selected['score'], best, '静态训练排序')
    summaries = [json.loads(l) for l in (run / 'calibration-summary.jsonl').read_text().splitlines()]
    expected = set(itertools.product(m['scenarios'], m['budgets'], m['calibration_seeds'],
                                   ('uniform_guard', 'phase_guard', 'phase_local_guard'), range(6)))
    same(Counter((r['scenario'], r['budget_level'], r['seed'], r['method'], r['parameter_index']) for r in summaries), Counter(expected), '相同校准限额')
    tuning = load(run / 'calibration-scores.json')
    for method in ('uniform_guard', 'phase_guard', 'phase_local_guard'):
        scores = []
        for i, p in enumerate(frozen['parameters']):
            rs = [r for r in summaries if r['method'] == method and r['parameter_index'] == i]
            score = [sum(not r['feasible'] for r in rs), sum(r['excess'] for r in rs), sum(r['loss'] for r in rs) / len(rs),
                     sum(r['update_rpc_count'] for r in rs), p['gain_margin'], p['reserve_fraction']]
            same(next(r['score'] for r in tuning if r['method'] == method and r['parameter_index'] == i), score, '完整校准排序')
            scores.append((score, i))
        same(frozen['selected_indices'][method], min(scores)[1], '冻结参数只取校准')
    heldout = load(run / 'summary.json')
    same(Counter((r['scenario'], r['budget_level'], r['seed'], r['method']) for r in heldout),
         Counter(itertools.product(m['scenarios'], m['budgets'], m['test_seeds'], m['methods'])), '完整保留矩阵')
    totals = {group: Counter() for group in ('calibration', 'heldout')}
    for group, rs in (('calibration', summaries), ('heldout', heldout)):
        for row in rs:
            s, b, seed, method = row['scenario'], row['budget_level'], row['seed'], row['method']
            tail = f'-p{row["parameter_index"]}' if group == 'calibration' else ''
            folder = 'calibration-runs' if group == 'calibration' else 'runs'
            raw = load(run / folder / f'{s}-{b}-{seed}-{method}{tail}.json.gz')
            w = load(run / ('calibration-workloads' if group == 'calibration' else 'workloads') / f'{s}-{seed}.json.gz')
            same(raw['summary'], row, '原始汇总一致')
            trained = next(r['config'] for r in selections if r['scenario'] == s and r['budget_level'] == b)
            index = row['parameter_index'] if group == 'calibration' else frozen['selected_indices'].get(method, 0)
            parameters = frozen['parameters'][index] if method in ('uniform_guard', 'phase_guard', 'phase_local_guard') else dict(frozen['parameters'][0], gain_margin=0, reserve_fraction=0)
            totals[group].update(check_layered(w, raw, m['budget_definition'][s][b], trained, m, fees, parameters))
    solver = check_solver(run, m, fees, frozen)
    return {'status': 'passed', 'static_replays': training_count, 'static_budget_scores': training_count * 2,
            'solver': solver,
            'calibration_runs': len(summaries), 'heldout_runs': len(heldout), 'totals': {k: dict(v) for k, v in totals.items()}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--workers', type=int, default=6)
    a = p.parse_args()
    print(json.dumps(audit(a.run, a.workers), ensure_ascii=False))


if __name__ == '__main__':
    main()
