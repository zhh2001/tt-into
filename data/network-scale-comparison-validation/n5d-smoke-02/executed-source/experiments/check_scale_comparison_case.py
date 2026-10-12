"""独立核对规模六方法的真实帧、合法调用、单区间任务和文献目标。"""
import copy
from collections import Counter

from check_observation_wire import load, rows, identity, skipped_errors
from check_observation_matched import charges_for, direct_window
from check_rolling_reference import same, near, violation
from scalable_observation_check import replay
from scalable_adaptive_check import check_adaptive_raw
from scale_static_tasks import check_tasks
from check_scale_literature_decisions import check_decisions

METHODS = ('static', 'feedback', 'phase_joint', 'phase_guard', 'intopp_due', 'd_into_tt')


def check_case(directory, rpc):
    c, opts = load(directory / 'result.json'), load(directory / 'algorithm-settings.json')
    topology = c['topology']
    registry = {identity(f['identity']): f for f in topology['flows']}
    routes = {f: tuple(r['path']) for f, r in registry.items()}
    keys = {(node, f) for f, route in routes.items() for node in route}
    packets = load(directory / 'packets.json')
    business = [r for r in rows(directory / 'destination.jsonl') if r.get('kind') == 'frame' and r.get('src') == '10.0.0.1']
    reports = [r for r in rows(directory / 'collector.jsonl') if r.get('ether_type') == 0x88b6]
    got = {r['packet_id']: r for r in business}
    exported = {r['packet_id']: r for r in reports}
    sends = rows(directory / 'send.jsonl')
    actions, decisions = rows(directory / 'actions.jsonl'), rows(directory / 'decisions.jsonl')
    checks = 0

    def eq(a, b, why):
        nonlocal checks
        same(a, b, why); checks += 1

    def require(ok, why):
        nonlocal checks
        checks += 1
        if not ok: raise ValueError(why)

    eq(len(got), len(business), '业务接收身份唯一')
    eq(len(got), len(packets), '全部业务交付')
    eq({r['packet_id'] for r in sends}, set(got), '实际发送与接收集合')
    eq(len(sends), len(packets), '发送分母')
    eq(len(exported), len(reports), '报告身份唯一')
    eq(Counter(r['payload_hex'] for r in rows(directory / 'destination.jsonl') if r.get('kind') == 'udp_application'),
       Counter(p['payload_hex'] for p in packets), '全部UDP应用恢复')
    require(not any(r.get('socket_drop_counter', 0) or 'decode_error' in r for r in business + reports), '采集丢包或解码异常')
    sent = {r['packet_id']: r for r in sends}
    for p in packets:
        row, descriptor = got[p['packet_id']], topology['flows'][p['flow'] - 1]
        eq(row['transport_hex'], p['expected_transport_hex'], '原传输内容恢复')
        eq(row['ip_length'], p['ip_length'], '原IPv4长度恢复')
        eq(row['ttl'], 63 - len(p['path']), '逐跳TTL')
        require(row['ip_checksum_ok'], '业务校验和')
        eq(sent[p['packet_id']]['source_interface_index'], descriptor['source_interface_index'], '显式流发送接口')
        r = exported.get(p['packet_id'])
        if r:
            eq((r['session'], r['flow_tag']), identity(p['wire_identity']), '真实并发身份')
            eq((r['version'], r['count'], r['ip_length'], r['transport_hex']), (6, 1, 44, ''), '报告版本和K=1')
            require(r['ip_checksum_ok'], '报告校验和')
            if p['probe']:
                eq(r['records'][0]['generated_us'], 123, '预填探针未产生新记录')
            else:
                require(all(x['switch_id'] in p['path'] and x['hop'] == p['path'].index(x['switch_id']) + 1 for x in r['records']), '报告来自实际路径')
        if p['probe']: require(r is not None, '身份探针未导出')
    for a in actions:
        r = rpc[a['rpc_index']]
        descriptor = registry[identity(a['flow'])]
        require((a['node'], identity(a['flow'])) in keys, '配置越过管理路径')
        eq((r['success'], r['node']), (a['success'], a['node']), '动作对应真实返回')
        eq(r['arguments'], ['TTEgress.max_count' if a['field'] == 'enabled' else 'TTEgress.time_threshold',
                            descriptor['slot'], int(a['value'])], '实际字段和非连续流槽')
        require(r['request_serialized_bytes'] > 0 and r['reply_serialized_bytes'] > 0, '实际调用计费')
    for f in load(directory / 'final-state.json'):
        descriptor = registry[identity(f['flow'])]
        eq(f['slot'], descriptor['slot'], '最终寄存器槽')
        count = sum(p['flow'] == topology['flows'].index(descriptor) + 1 and p['registered'] for p in packets)
        eq(f['source_count'], count if f['node'] == descriptor['entry_node'] else 0, '源计数位于真实入口而非槽号节点')
        for field, actual in [('threshold_us', f['threshold_us']), ('enabled', bool(f['capacity']))]:
            history = [a for a in actions if a['success'] and a['node'] == f['node'] and identity(a['flow']) == identity(f['flow']) and a['field'] == field]
            eq(actual, history[-1]['value'] if history else 0, '实际最终配置或非路径零配置')
    count_rows = rows(directory / 'count-observations.jsonl')
    for read in count_rows:
        o, r = read['observation'], rpc[read['rpc_index']]
        descriptor = registry[identity(o['flow'])]
        eq((r['node'], r['method'], r['arguments'], r['result'], r['success']),
           (descriptor['entry_node'], 'bm_register_read', ['TTIngress.packet_count', descriptor['slot']], o['count'], True), '付费源计数真实返回与接口映射')
        eq((o['issued_us'], o['available_us']), ((r['before_monotonic_ns'] - c['origin_monotonic_ns']) // 1000,
           (r['after_monotonic_ns'] - c['origin_monotonic_ns']) // 1000), '源计数因果可用时间')
    observations = rows(directory / 'report-observations.jsonl')
    observed = replay((directory / 'collector.jsonl').read_bytes(), observations, decisions, c['origin_monotonic_ns'],
                      c['observation_period_us'], c['duration_us'], c['decision_period_us'], routes)
    eq([r['availability'] for r in observations if not r['skipped']], rows(directory / 'availability.jsonl'), '观察批次完整留档')
    charges = charges_for(directory, rpc)
    adaptive = []
    notices = rows(directory / 'notices.jsonl')
    for d in decisions:
        if 'view' not in d:
            eq(skipped_errors(d, c), [], '合法整周期跳过'); continue
        now, view = d['cutoff_ns'] - c['origin_monotonic_ns'], d['view']
        eq(now, view['now_us'] * 1000, '费用与观察截止一致')
        eq(view['counts'], [r['observation'] for r in count_rows if r['observation']['available_us'] <= view['now_us']], '只使用已返回计数')
        visible = [n for n in notices if n['received_ns'] <= d['cutoff_ns']]
        eq([n['version'] for n in view['notifications']], [n['message']['version'] for n in visible], '只使用已收管理需求')
        for confirmation in view['confirmations']:
            a = next(a for a in actions if a['id'] == confirmation['write_id'])
            require(a['success'] and confirmation['success'], '仅成功确认进入视图')
            eq(confirmation['received_us'], (a['confirmation_available_ns'] - c['origin_monotonic_ns']) // 1000, '实际配置确认时间')
            eq((confirmation['node'], identity(confirmation['flow']), confirmation['field'], confirmation['value']),
               (a['node'], identity(a['flow']), a['field'], a['value']), '确认来自实际写入')
            require(confirmation['received_us'] <= view['now_us'], '确认尚未可用')
        for field, cutoff in [('control_snapshot', now), ('after_control_snapshot', d['cycle_finished_ns'] - c['origin_monotonic_ns'])]:
            eq(d[field]['cutoff_ns'], cutoff, '自有账本截止')
            eq([{k: r[k] for k in charges[0]} for r in d[field]['charges']], [r for r in charges if r['known_ns'] <= cutoff], '完整合法控制费用前缀')
        require(len(d['writes']) <= 6, '字段更新限额')
        for a in d['writes']:
            r = rpc[a['rpc_index']]
            require(d['cutoff_ns'] <= r['before_monotonic_ns'] <= r['after_monotonic_ns'] <= d['cycle_finished_ns'], '因果配置更新')
        if 'selected' not in d: continue
        require(len(d['candidate_scores']) <= 48, '候选限额')
        require(d['cutoff_ns'] <= d['solver_started_ns'] <= d['solver_finished_ns'] <= d['cycle_finished_ns'], '求解实际顺序')
        for candidate in [d['selected'], *d['candidate_scores']]:
            require(len(candidate['config']) == len(keys) and all(v is None or v in opts['settings']['grid_us'] for v in candidate['config']), '完整配置维度和共同网格')
        for candidate in d['candidate_scores']:
            eq(candidate['update_allowed'], candidate['update_rpc_count'] <= 6, '候选写限额')
            near(candidate['predicted_excess'], violation(candidate['cost'], opts['budget']) + d['budget_diagnostic']['known_notice_debt'], '预测分层费用')
        diag = d['budget_diagnostic']
        eq(diag['control_ledger_window'], direct_window(charges, 2000000000, min(10000000000, max(2000000000, now + 1)), now), '实际已花分层费用')
        if c['method'] == 'static': eq((d['writes'], d['reads'], d['selected']['config']), ([], [], c['initial_config']), '静态配置和零在线计数')
        item = copy.deepcopy(d)
        item['budget_diagnostic']['spend']['control_bytes'] = diag['spend']['unused_legacy_control_prediction_bytes']
        item['budget_diagnostic']['effective_budget']['control_bytes_per_s'] = max(0, (8 * opts['budget']['control_bytes_per_s'] - item['budget_diagnostic']['spend']['control_bytes']) / diag['remaining_s']) * (1 - opts['adaptive']['reserve_fraction'])
        if view['notifications']: adaptive.append(item)
        else: eq(d['selected']['config'], d['current_config'], '无需求时保持配置')
    used_reads = [r['rpc_index'] for d in decisions for r in d.get('reads', [])]
    eq(used_reads, [i for i in range(c['rpc_start_index'], c['rpc_stop_index']) if rpc[i]['kind'] == 'online_observation'], '计数仅在决策周期读取')
    eq(len(used_reads), 0 if c['method'] == 'static' else len(registry) * sum('view' in d and not d['skipped'] for d in decisions), '多流计数数量与观察tick分离')
    eq([r['rpc_index'] for d in decisions for r in d.get('writes', [])], [i for i in range(c['rpc_start_index'], c['rpc_stop_index']) if rpc[i]['kind'] == 'update'], '全部在线写入归属')
    eq([{k: r[k] for k in charges[0]} for r in load(directory / 'control-ledger.json')['charges']], charges, '最终实际账本')
    if c['method'] in ('phase_joint', 'phase_guard'):
        checks += check_adaptive_raw({'summary': {'method': c['method']}, 'decisions': adaptive},
            {'settings': opts['settings'], 'topology': {'routes': [{'flow': f['identity'], 'objects': [dict(node=n, port=2, queue=0) for n in f['path']]} for f in topology['flows']]}}, opts['adaptive'])
    if c['method'] == 'manual':
        updates = [(a['node'], identity(a['flow']), a['field'], a['value']) for a in actions if rpc[a['rpc_index']]['kind'] == 'update' and a['success']]
        expected = [(f['entry_node'], identity(f['identity']), 'enabled', enabled) for enabled in (False, True) for f in topology['flows']]
        expected += [(3, identity(topology['flows'][-1]['identity']), 'threshold_us', 400000)]
        eq(updates, expected, '脚本许可停启及阈值真实更新')
        fresh = [r for o in observations for r in o.get('fresh_reports', [])]
        require(not any(x['generated_local_us'] == 123 for r in fresh for x in r['records']), '旧会话、旧时间及路径不符探针推进了缓存')
    tasks = load(directory / 'tasks.json')
    checks += check_tasks(topology, c['scenario'], tasks, load(directory / 'notice-schedule.json'))
    require(c['method'] in METHODS, '规模比较方法身份')
    literature_checks = 0
    if c['method'] in ('intopp_due', 'd_into_tt'):
        literature_checks = check_decisions(decisions, c, opts)
        checks += literature_checks
    sent_notices, planned = rows(directory / 'notice-send.jsonl'), load(directory / 'notice-schedule.json')
    eq([r['message']['version'] for r in notices], [r['message']['version'] for r in planned], '管理需求完整交付')
    for actual, send, plan in zip(notices, sent_notices, planned):
        eq(actual['payload_hex'], send['payload_hex'], '实收IPC编码')
        require(c['origin_monotonic_ns'] + plan['offset_us'] * 1000 <= send['before_ns'] <= send['after_ns'] and send['before_ns'] <= actual['received_ns'], '需求发送和接收因果顺序')
    return dict(status='passed', errors=[], packet_count=len(packets), report_count=len(reports), checks=checks,
                decision_count=len(decisions), successful_updates=sum(a['success'] and rpc[a['rpc_index']]['kind'] == 'update' for a in actions),
                paid_counts=len(count_rows), skipped_epochs=sum(d.get('skipped', False) for d in decisions), literature_checks=literature_checks, **observed)
