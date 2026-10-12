"""从真实调用与帧重建合法前缀、共同限额和已知费用，不导入策略。"""
import argparse
from collections import Counter
import copy
import hashlib
import itertools
import json
from pathlib import Path

from check_observation_wire import check_case as wire_case, load, rows, identity
from check_literature_decisions import check_decisions
from check_rolling_reference import near, same, violation


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def charges_for(directory, rpc):
    c = load(directory / 'result.json')
    origin, charges = c['origin_monotonic_ns'], []
    for i in range(c['rpc_start_index'], c['rpc_stop_index']):
        r = rpc[i]
        reg = r.get('arguments', [''])[0]
        if r['kind'] not in ('installation', 'cold_start_reset', 'online_observation', 'update', 'reconciliation', 'controlled_failed_read') \
                or r['method'] == 'bm_register_read_all' or isinstance(reg, str) and reg.startswith(('TTIngress.trace_', 'TTEgress.trace_')):
            continue
        for part, time, field in (('request', 'before_monotonic_ns', 'request_serialized_bytes'),
                                  ('reply', 'after_monotonic_ns', 'reply_serialized_bytes')):
            charges.append({'operation_id': f'rpc-{i}', 'layer': 'api_serialized', 'kind': r['kind'], 'part': part,
                'time_ns': r[time] - origin, 'known_ns': r['after_monotonic_ns'] - origin,
                'bytes': r[field], 'success': r['success']})
    for n in rows(directory / 'notices.jsonl'):
        body = bytes.fromhex(n['payload_hex'])
        same(json.loads(body), n['message'], '实收通知编码')
        same(len(body), n['payload_bytes'], '实收通知长度')
        charges.append({'operation_id': n['operation_id'], 'layer': 'local_notice_payload', 'kind': 'management_notice',
            'part': 'payload', 'time_ns': n['received_ns'] - origin, 'known_ns': n['received_ns'] - origin,
            'bytes': len(body), 'success': True})
    return sorted(charges, key=lambda r: (r['time_ns'], r['operation_id'], r['part']))


def direct_window(charges, start, end, cutoff):
    layers = ('api_serialized', 'local_notice_payload')
    total, kinds = dict.fromkeys(layers, 0), {k: {} for k in layers}
    for r in charges:
        if r['known_ns'] <= cutoff and start <= r['time_ns'] < end:
            total[r['layer']] += r['bytes']
            kinds[r['layer']][r['kind']] = kinds[r['layer']].get(r['kind'], 0) + r['bytes']
    return {'window_ns': [start, end], 'bytes_by_layer': total, 'bytes_by_layer_and_kind': kinds}


def check_case(directory, rpc):
    result = wire_case(directory, rpc)
    c, opts = load(directory / 'result.json'), load(directory / 'algorithm-settings.json')
    charges = charges_for(directory, rpc)
    decisions = rows(directory / 'decisions.jsonl')
    grid = set(itertools.product((None,) + tuple(opts['settings']['grid_us']), repeat=4))
    counts = 0
    origin = c['origin_monotonic_ns']
    adapted = []
    for d in decisions:
        if d.get('skipped') and 'view' not in d:
            if d['cycle_started_ns'] < origin + d['scheduled_offset_us'] * 1000 + 1000000000:
                raise ValueError('没有整周期迟到却跳过')
            continue
        now = d['cutoff_ns'] - origin
        same(now, d['view']['now_us'] * 1000, '真实费用与观察的共同截止')
        for field, cutoff in (('control_snapshot', now), ('after_control_snapshot', d['cycle_finished_ns'] - origin)):
            snapshot = d[field]
            same(snapshot['cutoff_ns'], cutoff, '自有前缀截止')
            expected = [r for r in charges if r['known_ns'] <= cutoff]
            same([{k: r[k] for k in charges[0]} for r in snapshot['charges']], expected, '真实自有调用/通知合法前缀')
            counts += len(expected)
        if 'selected' not in d:
            continue
        same(d['budget_diagnostic']['control_ledger_window'], direct_window(charges, 2000000000,
             min(10000000000, max(2000000000, now + 1)), now), '真实已花分层窗口')
        if not (origin + d['scheduled_offset_us'] * 1000 <= d['cycle_started_ns'] <= d['cutoff_ns'] <= d['solver_started_ns'] <= d['solver_finished_ns'] <= d['cycle_finished_ns']):
            raise ValueError('真实求解/周期时序错误')
        if len(d['writes']) > 6 or len(d['candidate_scores']) > 48 or tuple(d['selected']['config']) not in grid:
            raise ValueError('共同配置、候选或字段写限额错误')
        if c['method'] == 'static' and (d['writes'] or d['reads'] or d['selected']['config'] != c['initial_config']):
            raise ValueError('静态改变配置或进行无用途计数')
        for a in d['writes']:
            r = rpc[a['rpc_index']]
            if r['before_monotonic_ns'] < d['solver_finished_ns'] or r['after_monotonic_ns'] > d['cycle_finished_ns']:
                raise ValueError('配置写没有在真实求解结束后执行')
        for read in d['reads']:
            r = rpc[read['rpc_index']]
            if r['before_monotonic_ns'] < d['solver_finished_ns'] or r['after_monotonic_ns'] > d['cycle_finished_ns']:
                raise ValueError('源计数没有在真实求解结束后取得')
        for r in d['candidate_scores']:
            if tuple(r['config']) not in grid:
                raise ValueError('候选不属于共同网格')
            same(r['update_allowed'], r['update_rpc_count'] <= 6, '候选更新限额')
            near(r['predicted_excess'], violation(r['cost'], opts['budget']) + d['budget_diagnostic']['known_notice_debt'], '候选分层费用')
        counts += 6 + len(d['candidate_scores']) * 3
    final = load(directory / 'control-ledger.json')
    same([{k: r[k] for k in charges[0]} for r in final['charges']], charges, '最终自有账本')
    counts += check_decisions(decisions, c, opts)
    result.update(live_control_checks=counts, decision_count=len(decisions), skipped_epochs=sum(d.get('skipped', False) for d in decisions))
    return result


def audit(run):
    m = load(run / 'manifest.json')
    same(m['status'], 'passed', '运行完成')
    same(len(m['cases']), m['expected_cases'], '案例完整数')
    expected = Counter((profile, method) for profile in m['profiles'] for method in m['methods'])
    same(Counter((c['scenario'] + '-' + c['budget_level'], c['method']) for c in m['cases']), expected, '完整开发矩阵')
    for name, h in m['source_sha256'].items():
        same(sha(run / 'executed-source' / name), h, '实际执行源码')
    for name, h in m['artifact_sha256'].items():
        same(sha(run / name), h, '实际原始产物')
    rpc = rows(run / 'rpc-operations.jsonl')
    cases = [check_case(run / c['name'], rpc) for c in m['cases']]
    if any(c['status'] != 'passed' for c in cases):
        raise ValueError('真实线格式/交付/确认检查失败')
    return {'status': 'passed', 'cases': cases, 'packets': sum(c['packet_count'] for c in cases),
            'reports': sum(c['report_count'] for c in cases), 'decisions': sum(c['decision_count'] for c in cases),
            'checks': sum(c['checks'] + c['live_control_checks'] for c in cases)}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    print(json.dumps(audit(p.parse_args().run), ensure_ascii=False))
