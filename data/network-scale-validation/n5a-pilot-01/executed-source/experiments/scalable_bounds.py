"""通用合流网络的实际逐链路费用与共享任务年龄界。"""
from collections import Counter
from contextlib import closing
import json
from pathlib import Path

from scapy.utils import RawPcapReader
from check_observation_wire import load, rows, wire
from check_rolling_reference import clipped_loss


def wall_interval(samples):
    if not samples or any(r['before_monotonic_ns'] > r['after_monotonic_ns'] for r in samples):
        raise ValueError('墙钟/单调时钟夹取数据错误')
    lower = max(r['wall_ns'] - r['after_monotonic_ns'] for r in samples)
    upper = min(r['wall_ns'] - r['before_monotonic_ns'] for r in samples)
    return {'compatible': lower <= upper, 'lower_ns': lower, 'upper_ns': upper,
            'scope': '采样间墙钟与单调时钟恒定偏移的条件交集，不保证未观察跳变不存在'}


def member_bounds(lower, upper, start, end):
    if lower > upper:
        raise ValueError('输出时刻区间倒序')
    return lower >= start and upper < end, upper >= start and lower < end


def output_frames(run, cases):
    by_pid = {p['packet_id']: (c, p) for c in cases for p in load(run / c['name'] / 'packets.json')}
    if len(by_pid) != sum(len(load(run / c['name'] / 'packets.json')) for c in cases):
        raise ValueError('不同案例抓包验证身份重复')
    outputs, seen, counts = {c['name']: [] for c in cases}, set(), Counter()
    for path in sorted(run.glob('network-*/*/*_out.pcap')):
        node = int(path.parent.name.split('-')[1])
        port = 2 if path.name.endswith('-eth2_out.pcap') else 3 if path.name.endswith('-eth3_out.pcap') else None
        if port is None:
            continue
        with closing(RawPcapReader(str(path))) as stream:
            for frame, meta in stream:
                if len(frame) < 34 or frame[26:34] != b'\x0a\x00\x00\x01\x0a\x00\x00\x02':
                    continue
                r = wire(frame)
                if not r:
                    continue
                pid = r['packet_id']
                if pid not in by_pid or (pid, node, port) in seen:
                    raise ValueError('未知或重复业务输出')
                seen.add((pid, node, port))
                c, p = by_pid[pid]
                if path.parent.parent.name != 'network-' + c['network_id']:
                    raise ValueError('业务进入其他拓扑抓包')
                sink = c['topology']['sink']
                if port == 3 and node != sink:
                    continue
                extra = r['ip_length'] - p['ip_length']
                if node < sink:
                    if node not in p['path'] or extra != 12 + 12 * len(r['records']) or len(r['records']) > 1 \
                            or r['flow'] != (p['wire_identity']['session'], p['wire_identity']['tag']):
                        raise ValueError('实际路径、基础头或 K/身份错误')
                    resource, size = f'e{node}', extra
                elif port == 2:
                    if extra or r['flow'] or r['transport_hex'] != p['expected_transport_hex']:
                        raise ValueError('sink 未恢复业务')
                    resource, size = 'business', len(frame)
                else:
                    if r['ip_length'] != 44 or len(r['records']) != 1:
                        raise ValueError('导出尝试格式错误')
                    resource, size = 'export', max(60, len(frame))
                precision = 1 if stream.nano else 1000
                stamp = meta.sec * 1000000000 + meta.usec * precision
                outputs[c['name']].append({'packet_id': pid, 'node': node, 'port': port, 'resource': resource,
                    'bytes': size, 'wall_ns': stamp, 'precision_ns': precision, 'frame_bytes': len(frame)})
                counts[c['name'], node, port] += 1
    for c in cases:
        ps = load(run / c['name'] / 'packets.json')
        for node in range(1, c['topology']['sampling_nodes'] + 1):
            if counts[c['name'], node, 2] != sum(node in p['path'] for p in ps):
                raise ValueError('业务链路输出不完整')
        if counts[c['name'], c['topology']['sink'], 2] != len(ps) or counts[c['name'], c['topology']['sink'], 3] != c['report_count']:
            raise ValueError('业务/报告输出不完整')
    return outputs


def costs(directory, case, outputs, charges):
    clock = wall_interval(load(directory / 'wall-clock-samples.json'))
    origin = case['origin_monotonic_ns']
    start, end = origin + 2000000000, origin + 10000000000
    resources = [*(f'e{n}' for n in range(1, case['topology']['sampling_nodes'] + 1)), 'export']
    lower, upper, total = dict.fromkeys(resources, 0), dict.fromkeys(resources, 0), Counter()
    for r in outputs:
        if r['resource'] == 'business':
            continue
        total[r['resource']] += r['bytes']
        if clock['compatible']:
            lo, hi = r['wall_ns'] - clock['upper_ns'], r['wall_ns'] + r['precision_ns'] - clock['lower_ns']
            definitely, possibly = member_bounds(lo, hi, start, end)
            lower[r['resource']] += r['bytes'] * definitely
            upper[r['resource']] += r['bytes'] * possibly
    api = sum(r['bytes'] for r in charges if r['layer'] == 'api_serialized' and 2000000000 <= r['time_ns'] < 10000000000)
    local = sum(r['bytes'] for r in charges if r['layer'] == 'local_notice_payload' and 2000000000 <= r['time_ns'] < 10000000000)
    opts = load(directory / 'algorithm-settings.json')
    budget = dict(opts['budget']['link_bytes_per_s'], export=opts['budget']['export_bytes_per_s'])
    api_ok = api / 8 <= opts['budget']['control_bytes_per_s'] and local / 8 <= opts['notice_budget_bytes_per_s']
    status = 'unavailable' if not clock['compatible'] else 'within' if api_ok and all(upper[k] / 8 <= budget[k] for k in budget) \
        else 'exceeds' if not api_ok or any(lower[k] / 8 > budget[k] for k in budget) else 'ambiguous'
    return {'wall_clock': clock, 'whole_case_data_bytes': dict(total), 'measurement_lower_bytes': lower if clock['compatible'] else None,
            'measurement_upper_bytes': upper if clock['compatible'] else None,
            'api_serialized_bytes': api, 'api_bytes_per_s': api / 8, 'local_notice_payload_bytes': local,
            'local_notice_bytes_per_s': local / 8, 'window_feasibility': status,
            'scope': '输出抓包时刻及恒定墙钟偏移的条件完成窗口界，非在线硬预算'}


def generation_bounds(directory, case):
    origin = case['origin_monotonic_ns']
    sends = {r['packet_id']: r for r in rows(directory / 'send.jsonl')}
    business = {r['packet_id']: r for r in rows(directory / 'destination.jsonl') if r.get('kind') == 'frame' and r.get('src') == '10.0.0.1'}
    probes = {p['packet_id'] for p in load(directory / 'packets.json') if p['probe']}
    raw, offsets = [], {n: [] for n in range(1, case['topology']['sampling_nodes'] + 1)}
    for r in rows(directory / 'collector.jsonl'):
        if r.get('ether_type') != 0x88b6 or r['packet_id'] in probes:
            continue
        lower = sends[r['packet_id']]['send_before_monotonic_ns']
        upper = min(business[r['packet_id']]['receive_monotonic_ns'], r['receive_monotonic_ns'])
        if lower > upper:
            raise ValueError('报告生成不在因果区间')
        for record in r['records']:
            node, stamp = record['switch_id'], record['generated_us'] * 1000
            # 交换机微秒时间戳向下取整，未知不足 1 us 的余量保留。
            offsets[node].append((lower - stamp - 1000, upper - stamp))
            raw.append({'packet_id': r['packet_id'], 'node': node, 'local_ns': stamp,
                        'lower_ns': lower, 'upper_ns': upper, 'received_ns': r['receive_monotonic_ns']})
    clocks = {str(n): {'compatible': max(a for a, _ in pairs) <= min(b for _, b in pairs),
                      'lower_ns': max(a for a, _ in pairs), 'upper_ns': min(b for _, b in pairs)}
              if pairs else {'compatible': None, 'lower_ns': None, 'upper_ns': None} for n, pairs in offsets.items()}
    intervals, lower_samples, upper_samples, cache = [], [], [], {}
    coverage = []
    for r in sorted(raw, key=lambda r: (r['received_ns'], r['packet_id'])):
        clock = clocks[str(r['node'])]
        lo, hi = r['lower_ns'], r['upper_ns']
        if clock['compatible']:
            lo, hi = max(lo, r['local_ns'] + clock['lower_ns']), min(hi, r['local_ns'] + clock['upper_ns'] + 1000)
        if lo > hi:
            raise ValueError('生成区间缩紧后倒序')
        obj = {'node': r['node'], 'port': 2, 'queue': 0}
        receipt = (r['received_ns'] - origin) / 1000
        lower_samples.append({'object_id': obj, 'generated_us': (lo - origin) / 1000, 'received_us': receipt})
        upper_samples.append({'object_id': obj, 'generated_us': (hi - origin) / 1000, 'received_us': receipt})
        intervals.append(dict(r, generation_lower_ns=lo, generation_upper_ns=hi))
        previous = cache.get(r['node'])
        if previous is None or 0 < (r['local_ns'] // 1000 - previous) % 2**48 < 2**47:
            cache[r['node']] = r['local_ns'] // 1000
            coverage.append((obj, receipt))
    tasks = load(directory / 'tasks.json')
    # 生成上界对应年龄下界，生成下界对应年龄上界。
    low = clipped_loss(tasks, upper_samples, 4)[0]
    high = clipped_loss(tasks, lower_samples, 4)[0]
    if low > high + 1e-12:
        raise ValueError('连续年龄损失界倒序')
    covered = sum(t['weight'] for t in tasks if any(o == t['object_id'] and t['start_us'] <= at < t['end_us'] for o, at in coverage))
    return {'local_clock_offset': clocks, 'intervals': intervals, 'loss_lower': low, 'loss_upper': high,
            'weighted_active_interval_coverage': covered / sum(t['weight'] for t in tasks),
            'scope': '同宿主因果区间，同节点恒定偏移相容时缩紧；不相容时保留宽界，非精确同步 AoI'}
