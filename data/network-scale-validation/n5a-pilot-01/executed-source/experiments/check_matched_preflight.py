"""从原调用日志直接核对费用前缀和共同候选，不导入策略或账本。"""
import argparse
from collections import Counter
import gzip
import hashlib
from itertools import product
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRID = (50000, 100000, 150000, 200000, 400000, 600000, 800000, 1000000)
KINDS = ('installation', 'cold_start_reset', 'online_observation', 'update', 'reconciliation', 'controlled_failed_read')
LAYERS = ('api_serialized', 'local_notice_payload')


def load(path):
    if path.suffix == '.gz':
        with gzip.open(path, 'rt') as stream:
            return json.load(stream)
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def direct_window(charges, start, end, cutoff):
    by_layer, by_kind = dict.fromkeys(LAYERS, 0), {k: {} for k in LAYERS}
    for r in charges:
        if r['known_ns'] <= cutoff and start <= r['time_ns'] < end:
            by_layer[r['layer']] += r['bytes']
            by_kind[r['layer']][r['kind']] = by_kind[r['layer']].get(r['kind'], 0) + r['bytes']
    return {'window_ns': [start, end], 'bytes_by_layer': by_layer, 'bytes_by_layer_and_kind': by_kind}


def check_case_ledger(entry, operations, notices):
    origin, expected = entry['origin_ns'], []
    for i, r in enumerate(operations):
        register = r.get('arguments', [''])[0]
        if r['kind'] not in KINDS or r['method'] == 'bm_register_read_all' or register.startswith(('TTIngress.trace_', 'TTEgress.trace_')):
            continue
        for part, time, field in (('request', r['before_monotonic_ns'], 'request_serialized_bytes'),
                                  ('reply', r['after_monotonic_ns'], 'reply_serialized_bytes')):
            expected.append({'operation_id': f'rpc-{i}', 'layer': LAYERS[0], 'kind': r['kind'], 'part': part,
                             'time_ns': time - origin, 'known_ns': r['after_monotonic_ns'] - origin,
                             'bytes': r[field], 'success': r['success']})
    for case, entries in notices.items():
        for i, r in enumerate(entries):
            body = bytes.fromhex(r['payload_hex'])
            require(len(body) == r['payload_bytes'] and json.loads(body) == r['message'], '通知实际字节与内容不一致')
            expected.append({'operation_id': f'{case}-notice-{i}', 'layer': LAYERS[1], 'kind': 'management_notice',
                             'part': 'payload', 'time_ns': r['received_ns'] - origin, 'known_ns': r['received_ns'] - origin,
                             'bytes': len(body), 'success': True})
    keys = lambda r: (r['operation_id'], r['layer'], r['part'])
    columns = tuple(expected[0])
    actual = [{k: r[k] for k in columns} for r in entry['charges']]
    require(len({keys(r) for r in actual}) == len(actual), '重复费用身份')
    require(sorted(actual, key=keys) == sorted(expected, key=keys), '费用不是自有实收调用与通知的准确归因')
    count = len(expected)
    for prefix in entry['prefixes']:
        cutoff = prefix['cutoff_ns']
        require(prefix['known_charge_count'] == sum(r['known_ns'] <= cutoff for r in expected), '未来费用暴露或前缀遗漏')
        require(prefix['window'] == direct_window(expected, 2000000000, min(10000000000, max(2000000000, cutoff + 1)), cutoff),
                '前缀窗口费用错误')
        count += 2
    start, end = entry['measurement_window_ns']
    require(entry['final_usage'] == direct_window(expected, start, end, entry['final_cutoff_ns']), '测量窗口分层费用错误')
    return count + 1


def rank(row, local=False):
    return (round(row['effective_budget_excess'], 12), row['local_loss' if local else 'predicted_loss'],
            row['update_rpc_count'], tuple(-1 if v is None else v for v in row['config']))


def check_solver(run):
    d = load(run / 'solver-diagnostic.json')
    candidates = load(run / 'exact-candidates.json.gz')
    require(len(candidates) == 6561 and {tuple(r['config']) for r in candidates} == set(product((None,) + GRID, repeat=4)),
            '共同配置集合缺失或重复')
    fields, versions = {}, {}
    for r in d['input_view']['confirmations']:
        if not r['success']:
            continue
        key = r['node'], r['flow']['session'], r['flow']['tag'], r['field']
        stamp = r['version'], r['received_us']
        if stamp >= versions.get(key, (-1, -10**30)):
            fields[key], versions[key] = r['value'], stamp
    keys = ((1, 20, 1001), (3, 20, 1001), (2, 20, 2002), (3, 20, 2002))
    fees = load(run / 'rpc-reference.json')['calls']
    byte_sum = lambda field: fees[field]['request_bytes'] + fees[field]['reply_bytes']
    eligible = []
    for r in candidates:
        writes, cost, touched = 0, 0, set()
        for key, val in zip(keys, r['config']):
            enabled, threshold = fields.get(key + ('enabled',), False), fields.get(key + ('threshold_us',), 100000)
            changed = ['enabled'] if val is None and enabled else []
            if val is not None:
                if threshold != val:
                    changed.append('threshold_us')
                if not enabled:
                    changed.append('enabled')
            writes += len(changed)
            if changed:
                touched.add(key)
            cost += sum(byte_sum(k) for k in changed)
        require(r['update_rpc_count'] == writes and r['update_allowed'] == (writes <= 6), '候选调用或限额错误')
        require(r['cost']['control_serialized_bytes_by_kind'] == {'installation': 0, 'observation': 2 * byte_sum('read'), 'update': cost},
                '候选重复收费通知或读写费用错误')
        require(r['cost']['control_request_count_by_kind'] == {'installation': 0, 'observation': 2, 'update': writes}
                and r['cost']['updated_entry_count'] == len(touched), '候选调用和实际修改键统计错误')
        if writes <= 6:
            eligible.append(r)
    require(d['eligible_count'] == len(eligible), '可执行候选数错误')
    for suffix, local in (('joint', False), ('local', True)):
        best = min(eligible, key=lambda r: rank(r, local))
        require(d['exact_' + suffix] == best, '穷举参照未按声明排序选择')
        limited = d['limited_' + suffix]
        require(limited['evaluated_candidates'] <= 48 and limited['forecast'] in candidates, '有限搜索结果或限额错误')
        require(d[suffix + '_rank_matches'] == (rank(best, local) == rank(limited['forecast'], local)), '搜索差距报告错误')
    return len(candidates) * 2 + 5


def audit(run):
    m = load(run / 'manifest.json')
    require(m['status'] == 'passed' and not m['reserved_workloads_generated'], '验收未完成或已消费新输入')
    for name, digest in m['source_sha256'].items():
        require(sha(run / 'executed-source' / name) == digest, '执行源码摘要错误')
    for name, digest in m['artifact_sha256'].items():
        require(sha(run / name) == digest, '产物摘要错误')
    source = ROOT / m['n3_path']
    outer = load(source / 'manifest.json')
    require(sha(source / 'manifest.json') == m['n3_manifest_sha256'], 'N3 来源摘要错误')
    for name, digest in outer['artifact_sha256'].items():
        require(sha(source / name) == digest, 'N3 产物摘要错误')
    checks = 0
    entries = load(run / 'ledger-cases.json.gz')
    require(len(entries) == m['cases'] == 10, '案例覆盖不完整')
    for entry in entries:
        base = source / entry['variant']
        manifest = load(base / 'manifest.json')
        notices = {c['name']: rows(base / c['name'] / 'notices.jsonl') for c in manifest['cases']}
        ops = rows(base / 'rpc-operations.jsonl')
        checks += check_case_ledger(entry, ops, notices)
        case = next(c for c in manifest['cases'] if c['name'] == entry['case'])
        direct = entry['final_usage']['bytes_by_layer']
        require(direct[LAYERS[0]] == case['measured_cost']['online_api_serialized_bytes_in_window'] and
                direct[LAYERS[1]] == case['measured_cost']['local_notice_payload_bytes_in_window'], '与 N3 窗口费用不一致')
    operations = [r for variant in ('ordinary', 'diagnostics') for r in rows(source / variant / 'rpc-operations.jsonl')]
    for key, fee in load(run / 'rpc-reference.json')['calls'].items():
        matched = [r for r in operations if r['method'] == fee['method'] and r['arguments'][0] == fee['register']
                   and r['success'] == fee['success'] and (key != 'reconciliation' or r['kind'] == 'reconciliation')]
        require(len(matched) == fee['matched_calls'] and {(r['request_serialized_bytes'], r['reply_serialized_bytes']) for r in matched} ==
                {(fee['request_bytes'], fee['reply_bytes'])}, '费用参照未对应实际调用')
    checks += check_solver(run)
    smoke = load(run / 'interface-smoke.json.gz')
    require(len(smoke) == m['interface_smoke_decisions'] == 80, '八方法接口验收数量错误')
    require(Counter(r['method'] for r in smoke) == Counter({k: 10 for k in m['methods']}), '接口验收方法覆盖错误')
    return {'status': 'passed', 'cases': len(entries), 'ledger_charges': sum(len(r['charges']) for r in entries),
            'decision_prefixes': sum(len(r['prefixes']) for r in entries), 'checks': checks,
            'solver_candidates': 6561, 'interface_smoke_decisions': len(smoke)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.run), ensure_ascii=False))


if __name__ == '__main__':
    main()
