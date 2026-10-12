"""独立核对手工案例、逐链路字节和整数区间上的时效积分。"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct


def object_key(obj):
    return obj['node'], obj['port'], obj['queue']


def flow_key(flow):
    return flow['session'], flow['tag']


def check_case(spec, result):
    checks = []

    def equal(name, actual, expected):
        if actual != expected:
            raise ValueError(f'{name} 不一致: {actual!r} != {expected!r}')
        checks.append(name)

    def near(name, actual, expected):
        if not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError(name + ' 不一致')
        checks.append(name)

    for name, expected in spec['expected'].items():
        equal('hand-' + name, result[name], expected)
    # 前缀插入的最终记录在各链路之前已插入的子集。此核对不运行被测调度器。
    nodes = {n['node']: n for n in spec['nodes']}
    wire, exports, received, dropped, receipts = {}, 0, 0, [], []
    payloads = {row['packet_id']: bytes.fromhex(row['payload_hex']) for row in result['exports']}
    for packet in spec['packets']:
        chosen = result['selected'][str(packet['packet_id'])]
        route = packet['hops']
        header = packet['ip_bytes'] + 12 <= route[0]['mtu']
        delivered = True
        for i, hop in enumerate(route):
            node = hop['object_id']['node']
            wire.setdefault(hop['link'], 0)
            previous = sum(h['object_id']['node'] in chosen for h in route[:i])
            original_incoming = packet['ip_bytes'] + (12 if header and i else 0) + 12 * previous
            if original_incoming > hop['mtu']:
                dropped.append(packet['packet_id'])
                delivered = False
                break
            prefix = sum(h['object_id']['node'] in chosen for h in route[:i + 1])
            wire[hop['link']] += (12 if header else 0) + 12 * prefix
        for hop in route:
            wire.setdefault(hop['link'], 0)
        if chosen and delivered and packet['export_emitted']:
            size = max(60, 34 + 12 + 12 * len(chosen))
            exports += size
            received += size * len(packet['report_arrivals_us'])
            payload = payloads[packet['packet_id']]
            count, ttl, version, metric, tag, session = struct.unpack('!BBBBII', payload[:12])
            equal(f'header-{packet["packet_id"]}', (count, ttl, version, metric, tag, session),
                  (len(chosen), 255, 6, 1, packet['flow']['tag'], packet['flow']['session']))
            equal(f'payload-length-{packet["packet_id"]}', len(payload), 12 + 12 * len(chosen))
            for position, node in enumerate(chosen):
                hop_index = next(i for i, h in enumerate(route) if h['object_id']['node'] == node)
                hop = route[hop_index]
                offset = 12 + 12 * position
                clock = nodes[node]
                expected_record = bytes((node, hop_index + 1)) + \
                    ((hop['egress_us'] + clock['offset_us']) % (1 << clock['clock_bits'])).to_bytes(6, 'big') + \
                    hop['queue_packets'].to_bytes(4, 'big')
                equal(f'record-{packet["packet_id"]}-{position}', payload[offset:offset + 12], expected_record)
                receipts.extend((object_key(hop['object_id']), hop['egress_us'], at)
                                for at in packet['report_arrivals_us'])
    equal('independent-wire', result['inband_per_link_bytes'], wire)
    equal('independent-export-attempts', result['export_attempt_bytes'], exports)
    equal('independent-received-bytes', result['received_report_bytes'], received)
    equal('independent-drops', result['dropped_packets'], sorted(dropped))
    expected_control = dict.fromkeys(('installation', 'observation', 'update'), 0)
    for w in spec['writes']:
        expected_control[w['kind']] += w['cost']['request_bytes'] + w['cost']['reply_bytes']
    for r in spec['reads']:
        expected_control['observation'] += r['cost']['request_bytes'] + r['cost']['reply_bytes']
    equal('independent-control', result['control_serialized_bytes_by_kind'], expected_control)

    # 本验证集所有目标、生成/到达时间和截断转折均在整数微秒上。
    # 每 1 us 区间中点对分段线性年龄的面积精确，独立于解析积分实现。
    weighted_area, weighted_duration = 0.0, 0.0
    metrics = {r['task_id']: r for r in result['task_metrics']['tasks']}
    for task in spec['tasks']:
        key = object_key(task['object_id'])
        samples = sorted((a, g) for obj, g, a in receipts if obj == key)
        effective, latest = [], None
        for arrived, generated in samples:
            if latest is None or generated > latest:
                effective.append(arrived)
                latest = generated
        area, violation, unknown, known_area, recency_area, recency_unknown = 0.0, 0, 0, 0.0, 0.0, 0
        for left in range(task['start_us'], task['end_us']):
            midpoint = left + 0.5
            seen = [g for a, g in samples if a <= midpoint]
            if not seen:
                unknown += 1
                violation += 1
                area += spec['clip']
            else:
                age = midpoint - max(seen)
                known_area += age
                violation += age > task['target_us']
                area += min(age / task['target_us'], spec['clip'])
            advanced = [a for a in effective if a <= midpoint]
            if advanced:
                recency_area += midpoint - max(advanced)
            else:
                recency_unknown += 1
        duration = task['end_us'] - task['start_us']
        row = metrics[task['task_id']]
        near('loss-' + task['task_id'], row['clipped_normalized_loss'], area / duration)
        near('unknown-' + task['task_id'], row['generation_age']['unknown_fraction'], unknown / duration)
        near('violation-' + task['task_id'], row['generation_age']['violation_fraction'], violation / duration)
        if unknown < duration:
            near('known-age-' + task['task_id'], row['generation_age']['mean_known_age_us'], known_area / (duration - unknown))
        if recency_unknown < duration:
            near('known-recency-' + task['task_id'], row['effective_receipt_recency']['mean_known_age_us'],
                 recency_area / (duration - recency_unknown))
        if unknown:
            equal('unknown-mean-' + task['task_id'], row['generation_age']['mean_age_us'], None)
        weighted_area += task['weight'] * area
        weighted_duration += task['weight'] * duration
    near('weighted-loss', result['task_metrics']['weighted_clipped_normalized_loss'], weighted_area / weighted_duration)
    equal('full-active-denominator', result['task_metrics']['weighted_active_duration_us'], weighted_duration)
    return checks


def audit(run):
    manifest = json.loads((run / 'manifest.json').read_text())
    if manifest['status'] != 'passed':
        raise ValueError('运行尚未完成或失败，保留原始记录')
    for name, digest in manifest['artifact_sha256'].items():
        if hashlib.sha256((run / name).read_bytes()).hexdigest() != digest:
            raise ValueError('产物摘要不一致: ' + name)
    for name, digest in manifest['source_sha256'].items():
        if hashlib.sha256((run / 'executed-source' / name).read_bytes()).hexdigest() != digest:
            raise ValueError('执行源码摘要不一致: ' + name)
    count = 0
    summary = json.loads((run / 'summary.json').read_text())
    for row in summary:
        folder = run / row['case']
        checks = check_case(json.loads((folder / 'specification.json').read_text()),
                            json.loads((folder / 'result.json').read_text()))
        if len(checks) != row['check_count'] or checks != json.loads((folder / 'checks.json').read_text()):
            raise ValueError('独立检查列表不一致')
        count += len(checks)
    if count != manifest['independent_checks'] or len(summary) != manifest['cases']:
        raise ValueError('运行汇总不一致')
    return {'cases': len(summary), 'independent_checks': count, 'status': 'passed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.run), ensure_ascii=False))
