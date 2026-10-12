"""独立核对版本 6 线格式、实际门控、观察前缀和控制调用。"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct

from check_adaptive_reference import check_adaptive_raw


def load(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []


def identity(flow):
    return flow['session'], flow['tag']


def wire(frame):
    if len(frame) < 34 or frame[12:14] not in (b'\x08\x00', b'\x88\xb6'):
        return None
    length, pid = struct.unpack('!HH', frame[16:20])
    if frame[14] != 0x45 or len(frame) < 14 + length:
        raise ValueError('IPv4 长度或选项错误')
    words = struct.unpack('!10H', frame[14:34])
    total = sum(words)
    while total >> 16:
        total = (total & 65535) + (total >> 16)
    if total != 65535:
        raise ValueError('IPv4 校验和错误')
    end, records, flow = 34, [], None
    if frame[12:14] == b'\x88\xb6':
        count, ttl, version, metric, tag, session = struct.unpack('!BBBBII', frame[34:46])
        end = 46 + count * 12
        if version != 6 or metric != 1 or count > 8 or end > 14 + length:
            raise ValueError('版本 6 线格式错误')
        for i in range(46, end, 12):
            records.append({'switch_id': frame[i], 'hop': frame[i + 1],
                            'generated_us': int.from_bytes(frame[i + 2:i + 8], 'big'),
                            'queue_packets': int.from_bytes(frame[i + 8:i + 12], 'big')})
        flow = session, tag
    return {'packet_id': pid, 'ip_length': length, 'ttl': frame[22], 'flow': flow, 'records': records,
            'transport_hex': frame[end:14 + length].hex(), 'frame_bytes': len(frame)}


def check_case(directory, rpc):
    case = load(directory / 'result.json')
    packets = load(directory / 'packets.json')
    business = [r for r in rows(directory / 'destination.jsonl') if r.get('kind') == 'frame' and r.get('src') == '10.0.0.1']
    reports = [r for r in rows(directory / 'collector.jsonl') if r.get('ether_type') == 0x88b6]
    got, exported = {r['packet_id']: r for r in business}, {r['packet_id']: r for r in reports}
    apps = Counter(r['payload_hex'] for r in rows(directory / 'destination.jsonl') if r.get('kind') == 'udp_application')
    sends = {r['packet_id']: r for r in rows(directory / 'send.jsonl')}
    actions, decisions = rows(directory / 'actions.jsonl'), rows(directory / 'decisions.jsonl')
    errors, checks = [], 0

    def require(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            errors.append(message)

    require(len(got) == len(business) == len(packets) and len(sends) == len(packets), '业务数量、唯一性或发送日志错误')
    require(len(exported) == len(reports), '报告重复')
    require(not any(r.get('socket_drop_counter', 0) or 'decode_error' in r for r in business + reports), '采集套接字丢包或解码错误')
    require(apps == Counter(p['payload_hex'] for p in packets), 'UDP 应用恢复不完整')
    for p in packets:
        r = got.get(p['packet_id'])
        require(r is not None and r['transport_hex'] == p['expected_transport_hex'] and r['ip_length'] == p['ip_length']
                and r['ip_checksum_ok'] and r['ttl'] == 63 - len(p['path']), '业务恢复错误 ' + str(p['packet_id']))
        report = exported.get(p['packet_id'])
        if report:
            expected = (19, 1001) if p['injected_stale_session'] else (20, 1001 if p['flow'] == 1 else 2002)
            require((report['session'], report['flow_tag']) == expected and report['version'] == 6 and report['count'] == 1
                    and report['ip_length'] == 44 and report['ip_checksum_ok'] and report['transport_hex'] == '', '报告身份或格式错误')
            require(all(r['switch_id'] in p['path'] and r['hop'] == p['path'].index(r['switch_id']) + 1 for r in report['records']), '路径身份错误')
    observations = rows(directory / 'observations.jsonl')
    if case['diagnostics']:
        obs = {(r['packet_id'], r['node']): r for r in observations}
        expected_keys = {(p['packet_id'], n) for p in packets for n in p['path']}
        require(set(obs) == expected_keys, '诊断缺包或访问了错误分支')
        gates, valid_keys = {}, set()
        by_pid = {p['packet_id']: p for p in packets}
        for node in (1, 2, 3):
            ordered = sorted((r for r in observations if r['node'] == node), key=lambda r: (r['trace_generated'], r['packet_id']))
            for o in ordered:
                p = by_pid[o['packet_id']]
                key = node, p['flow']
                require(o['trace_arrival'] > 0 and o['trace_generated'] >= o['trace_arrival'], '诊断时钟因果关系错误')
                if o['trace_registered'] and o['trace_telemetry'] and o['trace_threshold']:
                    require(o['trace_previous'] == gates.get(key, 0) and bool(o['trace_valid']) == (key in valid_keys), '最后成功状态被重置或门控顺序错误')
                    if not o['trace_valid'] or (o['trace_gate_time'] - o['trace_previous']) % 2**48 >= o['trace_threshold']:
                        expected = 3 if o['trace_count_before'] >= o['trace_capacity'] or o['trace_count_before'] >= 8 else \
                            4 if o['trace_ip_length'] > o['trace_mtu'] - 12 else 1
                    else:
                        expected = 2
                    require(o['trace_decision'] == expected, '门控/容量/MTU 决定错误')
                    if expected == 1:
                        gates[key] = o['trace_gate_time']
                        valid_keys.add(key)
                elif p['registered'] and p['ip_length'] > 1488 and node == p['flow']:
                    require(o['trace_decision'] == 4, '基础头 MTU 阻挡错误')
                else:
                    require(o['trace_decision'] == 5, '未知流或旧会话产生新采样')
        for p in packets:
            selected = [n for n in p['path'] if obs[p['packet_id'], n]['trace_decision'] == 1]
            report = exported.get(p['packet_id'])
            if p['injected_stale_session']:
                require(bool(report) and not selected, '旧会话转发或禁止新采样错误')
                continue
            require(bool(report) == bool(selected), '非空报告导出错误')
            if report:
                require([r['switch_id'] for r in report['records']] == list(reversed(selected)), '导出选择与真实诊断不一致')
                for r in report['records']:
                    o = obs[p['packet_id'], r['switch_id']]
                    require(r['generated_us'] == o['trace_generated'] and r['queue_packets'] == o['trace_value'], '状态值或生成时间与实际采样不一致')
            count = 0
            for n in p['path']:
                require(obs[p['packet_id'], n]['trace_count_before'] == count, '上游项传播错误')
                count += obs[p['packet_id'], n]['trace_decision'] == 1
        for final in load(directory / 'final-state.json'):
            key = final['node'], final['slot']
            require(final['last_timestamp'] == gates.get(key, 0) and bool(final['has_sample']) == (key in valid_keys), '最终成功门控错误')
        for flow in (1, 2):
            counter = case['initial_source_counts'][str(flow)]
            for o in sorted((r for r in observations if r['node'] == flow and by_pid[r['packet_id']]['registered']),
                            key=lambda r: r['trace_arrival']):
                counter = (counter + 1) % 2**32
                require(o['source_count'] == counter, '源端逐包计数错误')
    for final in load(directory / 'final-state.json'):
        expected = (case['initial_source_counts'][str(final['node'])] +
                    sum(p['flow'] == final['node'] and p['registered'] for p in packets)) % 2**32 \
                   if final['node'] == final['slot'] and final['node'] in (1, 2) else 0
        require(final['source_count'] == expected, '最终源计数错误')
    for a in actions:
        r = rpc[a['rpc_index']]
        require(r['success'] == a['success'] and r['node'] == a['node'] and r['kind'] in ('installation', 'update'), '动作与真实 RPC 不一致')
        if a['success']:
            name = 'TTEgress.max_count' if a['field'] == 'enabled' else 'TTEgress.time_threshold'
            require(r['arguments'] == [name, 1 if a['flow']['tag'] == 1001 else 2, int(a['value'])], '实际更新字段错误')
            require((r['request_serialized_bytes'], r['reply_serialized_bytes']) ==
                    ((89, 30) if a['field'] == 'enabled' else (94, 30)), '实际写入序列化费用不符')
        else:
            require(a['fault'] == 'controlled_missing_register' and r['request_serialized_bytes'] > 0 and r['reply_serialized_bytes'] > 0, '失败未真实计费')
    count_rows = rows(directory / 'count-observations.jsonl')
    for row in count_rows:
        o, r = row['observation'], rpc[row['rpc_index']]
        require(r['success'] and r['method'] == 'bm_register_read' and r['arguments'][0] == 'TTIngress.packet_count'
                and r['result'] == o['count'], '计数观察不是实际读返回')
        require((r['request_serialized_bytes'], r['reply_serialized_bytes']) == (81, 40), '读取序列化费用不符')
        require(o['issued_us'] == (r['before_monotonic_ns'] - case['origin_monotonic_ns']) // 1000 and
                o['available_us'] == (r['after_monotonic_ns'] - case['origin_monotonic_ns']) // 1000, '读取可用时刻错误')
    if observations:
        for o in observations:
            p = next(p for p in packets if p['packet_id'] == o['packet_id'])
            if not o['trace_threshold']:
                continue
            bounds = sends[p['packet_id']]['send_before_monotonic_ns'], got[p['packet_id']]['receive_monotonic_ns']
            for field, observed in (('threshold_us', o['trace_threshold']), ('enabled', bool(o['trace_capacity']))):
                matching = [a for a in actions if a['success'] and a['node'] == o['node'] and
                            a['flow']['tag'] == (1001 if p['flow'] == 1 else 2002) and a['field'] == field]
                completed = [a for a in matching if rpc[a['rpc_index']]['after_monotonic_ns'] < bounds[0]]
                possible = [completed[-1]['value']] if completed else []
                possible += [a['value'] for a in matching if rpc[a['rpc_index']]['before_monotonic_ns'] <= bounds[1]
                             and rpc[a['rpc_index']]['after_monotonic_ns'] >= bounds[0]]
                require(observed in possible, '包使用了实际成功 RPC 不可能提供的配置')
    for f in load(directory / 'final-state.json'):
        for field, actual in (('threshold_us', f['threshold_us']), ('enabled', bool(f['capacity']))):
            matching = [a for a in actions if a['success'] and a['node'] == f['node'] and
                        identity(a['flow']) == identity(f['flow']) and a['field'] == field]
            if matching:
                require(actual == matching[-1]['value'], '最终寄存器与成功调用不一致')
    accepted_keys = {(r['receive_monotonic_ns'] - case['origin_monotonic_ns']) // 1000:
                     r for r in reports if r['session'] == 20}
    notice_rows = rows(directory / 'notices.jsonl')
    for d in decisions:
        view = d['view']
        require(view['now_us'] == (d['cutoff_ns'] - case['origin_monotonic_ns']) // 1000, '观察截止时刻错误')
        expected_counts = [row['observation'] for row in count_rows if row['observation']['available_us'] <= view['now_us']]
        require(view['counts'] == expected_counts, '计数前缀缺失或包含未来')
        for r in view['reports']:
            source = accepted_keys.get(r['received_us'])
            require(source is not None and identity(r['flow']) == (source['session'], source['flow_tag']) and r['received_us'] <= view['now_us'], '报告使用验证关联或未来身份')
            if source:
                require(r['records'] == [{'node': x['switch_id'], 'hop': x['hop'], 'generated_local_us': x['generated_us'], 'queue_packets': x['queue_packets']}
                                        for x in source['records']], '在线记录不是实际线格式')
        for c in view['confirmations']:
            require(c['received_us'] <= view['now_us'] and c['success'], '非法确认进入视图')
            if not c['write_id'].startswith('reconcile-'):
                a = next((a for a in actions if a['id'] == c['write_id']), None)
                require(a is not None and a['success'] and c['value'] == a['value'] and
                        c['received_us'] == (a['confirmation_available_ns'] - case['origin_monotonic_ns']) // 1000, '确认未返回或失败被当成成功')
            else:
                rec = next((r for r in load(directory / 'reconciliation.json') if r['node'] == c['node'] and
                            identity(r['flow']) == identity(c['flow']) and r['field'] == c['field']), None)
                require(rec is not None and rec['value'] == c['value'] and
                        c['received_us'] == (rec['received_ns'] - case['origin_monotonic_ns']) // 1000, '核对确认不是实际返回值')
        visible = [n for n in notice_rows if n['received_ns'] <= d['cutoff_ns']]
        require([n['version'] for n in view['notifications']] == [n['message']['version'] for n in visible], '管理通知使用未来信息')
        require(not d['unresolved'] or 'selected' not in d, '不确定配置仍进入在线求解')
    if case['name'] == 'partial_failure':
        require(any(not a['success'] for a in actions) and any(d['unresolved'] for d in decisions), '没有发生预声明的部分失败或未知状态')
        require(any(r['kind'] == 'controlled_failed_read' and not r['success'] for r in rpc), '实际读取失败没有留档')
        require(bool(load(directory / 'reconciliation.json')), '失败后未付费核对实际值')
    provenance = 0
    if case['name'] == 'causal_loop':
        require(any(a['success'] and rpc[a['rpc_index']]['kind'] == 'update' for a in actions), '真实闭环没有产生变化')
        require(len(notice_rows) == 2, '通知未全部交付')
        for n in notice_rows:
            require(len(bytes.fromhex(n['payload_hex'])) == n['payload_bytes'] and
                    json.loads(bytes.fromhex(n['payload_hex'])) == n['message'], '管理通知费用或内容错误')
        pseudo = {'summary': {'method': 'phase_guard'}, 'decisions': [
            dict(d, time_us=d['view']['now_us']) for d in decisions if 'selected' in d]}
        manifest = {'settings': load(directory / 'algorithm-settings.json')['settings'], 'topology': {'routes': [
            {'flow': {'session': 20, 'tag': tag}, 'objects': [{'node': node, 'port': 2, 'queue': 0}, {'node': 3, 'port': 2, 'queue': 0}]}
            for node, tag in ((1, 1001), (2, 2002))]}}
        provenance = check_adaptive_raw(pseudo, manifest, load(directory / 'algorithm-settings.json')['adaptive'])
    return {'status': 'passed' if not errors else 'failed', 'errors': errors, 'packet_count': len(packets),
            'report_count': len(reports), 'checks': checks, 'adaptive_provenance_checks': provenance,
            'record_counts': [sum(x['switch_id'] == n for r in reports if r['session'] == 20 for x in r['records']) for n in (1, 2, 3)],
            'stale_session_reports': sum(r['session'] != 20 for r in reports),
            'successful_updates': sum(a['success'] and rpc[a['rpc_index']]['kind'] == 'update' for a in actions)}


def audit(run):
    manifest = load(run / 'manifest.json')
    if manifest['status'] != 'passed':
        raise ValueError('原始运行未通过')
    for name, digest in manifest['source_sha256'].items():
        if hashlib.sha256((run / 'executed-source' / name).read_bytes()).hexdigest() != digest:
            raise ValueError('测量源码损坏')
    for name, digest in manifest['artifact_sha256'].items():
        if hashlib.sha256((run / name).read_bytes()).hexdigest() != digest:
            raise ValueError('测量产物损坏 ' + name)
    rpc = rows(run / 'rpc-operations.jsonl')
    results = [check_case(run / c['name'], rpc) for c in manifest['cases']]
    if any(r['status'] != 'passed' for r in results):
        raise ValueError('独立案例核对失败')
    return results
