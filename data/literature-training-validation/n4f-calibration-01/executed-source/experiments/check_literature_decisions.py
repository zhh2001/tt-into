"""不调用策略，独立重建预测容量、文献目标、确认队列及候选排序。"""
import math

from check_rolling_reference import flow, same, near

ROUTES = {(20, 1001): (1, 3), (20, 2002): (2, 3)}
KEYS = tuple((n, f) for f, route in sorted(ROUTES.items()) for n in route)


def rates(view, alpha):
    previous, values = {}, {}
    for r in view['counts']:
        f = flow(r['flow'])
        if r['available_us'] > view['now_us']:
            raise ValueError('未来统计进入文献判断')
        if f in previous:
            old = previous[f]
            dt, count = r['available_us'] - old['available_us'], (r['count'] - old['count']) % 2**32
            if dt < 0: raise ValueError('付费计数响应倒序')
            if count >= 2**31:
                values[f] = None
            elif dt > 0:
                value = count * 1e6 / dt
                values[f] = value if values.get(f) is None else alpha * value + (1 - alpha) * values[f]
        previous[f] = r
    return values


def prediction(view, config, options, paid_rates):
    now, end = view['now_us'], view['now_us'] + options['horizon_us']
    gates, local = {}, {}
    for report in view['reports']:
        f = flow(report['flow'])
        if report['received_us'] > now: raise ValueError('未来报告进入预测')
        for record in report['records']:
            k = record['node'], f
            stamp = record['generated_local_us']
            if k not in local or 0 < (stamp - local[k]) % 2**48 < 2**47:
                local[k] = stamp
                gates[k] = report['received_us'] - options['nominal_report_delay_us'] - \
                    (len(ROUTES[f]) - record['hop']) * options['nominal_hop_us'] - 2 * options['nominal_egress_us']
    packets = []
    for f, route in ROUTES.items():
        rate = paid_rates.get(f)
        if rate is None or rate <= 0: continue
        spacing = 1e6 / min(rate, options['maximum_rate_pps'])
        t = now + ((f[1] % 31) + .5) / 31 * spacing
        while t < end:
            packets.append((math.ceil(t), len(packets), f, route))
            t += spacing
    samples, choices = dict.fromkeys((1, 2, 3), 0), []
    inband, exported = dict.fromkeys(('e1', 'e2', 'e3'), 0), 0
    cfg = dict(zip(KEYS, config))
    for source, pid, f, route in sorted(packets):
        carried, nodes = 0, []
        for index, n in enumerate(route):
            at = source + index * options['nominal_hop_us']
            threshold = cfg[n, f]
            if threshold is not None and carried < options['capacity'] and \
                    ((n, f) not in gates or (at - gates[n, f]) % 2**48 >= threshold):
                gates[n, f] = at
                carried += 1
                nodes.append(n)
            if at + options['nominal_egress_us'] < end:
                inband[f'e{n}'] += 12 + carried * 12
        sink = source + (len(route) - 1) * options['nominal_hop_us'] + 2 * options['nominal_egress_us']
        if nodes and sink < end: exported += max(60, 46 + 12 * len(nodes))
        if sink + options['nominal_report_delay_us'] < end:
            for n in nodes: samples[n] += 1
        choices.append(dict(packet_id=pid, nodes=nodes))
    return samples, choices, inband, exported


def check_decisions(decisions, case, opts):
    queue, previous, history, checks = 0.0, None, [], 0
    method, parameters, options = case['method'], opts['literature'], opts['settings']
    for d in decisions:
        if 'selected' not in d: continue
        v, state = d['view'], d['literature_state']
        same(state['method'], method, '文献方法身份')
        same(state['parameters'], parameters, '预设文献参数')
        for name, clock in (('cache', 'received_us'), ('notifications', 'received_us'), ('confirmations', 'received_us')):
            if any(row[clock] > v['now_us'] for row in v[name]): raise ValueError('文献视图含未来状态')
        fields = {k: dict(enabled=False, threshold_us=100000) for k in KEYS}
        versions = {}
        for c in v['confirmations']:
            k, field = (c['node'], flow(c['flow'])), c['field']
            if c['success'] and (k, field) in versions and (c['version'], c['received_us']) < versions[k, field]: continue
            if c['success']:
                versions[k, field] = c['version'], c['received_us']
                fields[k][field] = c['value']
        config = [fields[k]['threshold_us'] if fields[k]['enabled'] else None for k in KEYS]
        same(d['current_config'], config, '当前配置只取成功确认')
        bits = [value is not None for value in config]
        delta = 0 if previous is None else 12 * sum(a != b for a, b in zip(bits, previous))
        near(state['queue_before'], queue, '虚拟队列前态')
        same(state['previous_confirmed_assignment'], previous, '上个已确认许可')
        if method == 'd_into_tt': queue = max(0, queue + delta - parameters['average_change_bytes'])
        near(state['queue_after'], queue, '实际许可结算虚拟队列')
        same(state['current_confirmed_assignment'], bits, '当前已确认许可')
        same(state['settled_change_bytes'], delta, '结算许可变化')
        previous = bits
        expected_rates = rates(v, options['alpha'])
        for row in d['rates']:
            actual, expected = row['rate_pps'], expected_rates.get(flow(row['flow']))
            if expected is None: same(actual, None, '未知付费速率')
            else: near(actual, expected, '差分EWMA付费速率')
        same(d['profiles'], [], '文献试点使用均匀机会')
        same(d['budget_history'], history, '自身历史预测前缀')
        demands = {}
        for notification in v['notifications']:
            for demand in notification['demands']:
                n = demand['object_id']['node']
                if n not in demands or notification['version'] >= demands[n][0]:
                    demands[n] = notification['version'], demand
        active = {n: t for n, (_, t) in demands.items() if t['active']}
        cache = {r['object_id']['node']: r['received_us'] for r in v['cache']}
        valid = []
        for row in d['candidate_scores']:
            samples, choices, inband, exported = prediction(v, row['config'], options, expected_rates)
            same(row['predicted_packet_choices'], choices, '独立逐包容量与门控预测')
            same(row['cost']['inband_per_link_bytes'], inband, '独立预测带内字节')
            same(row['cost']['export_attempt_bytes'], exported, '独立预测导出尝试')
            inputs, info, complete = [], 0.0, 0.0
            for n, task in sorted(active.items()):
                recency = None if n not in cache else v['now_us'] - cache[n]
                due = recency is None or recency + parameters['early_fraction'] * options['horizon_us'] > task['target_us']
                attained = min(1, samples[n] * task['target_us'] / options['horizon_us'])
                info += task['weight'] * attained
                complete += (task['weight'] if parameters['weighted_dependencies'] else 1) * (due and samples[n] > 0)
                inputs.append(dict(node=n, weight=task['weight'], target_us=task['target_us'], recency_us=recency,
                    due=due, predicted_samples=samples[n], attained=attained))
            same(row['literature_inputs'], inputs, '合法期限与管理收益输入')
            near(row['information'], info, '频率目标达成收益')
            near(row['fulfilled_dependencies'], complete, '完整到期依赖数')
            change = 12 * sum((a is not None) != b for a, b in zip(row['config'], bits))
            same(row['assignment_change_bytes'], change, '许可变化包含迁移两位')
            objective = queue * change - parameters['value_scale'] * info if method == 'd_into_tt' else -complete
            near(row['literature_objective'], objective, '文献独立目标')
            nodes = [n for (n, _), a in zip(KEYS, row['config']) if a is not None]
            admissible = len(nodes) == len(set(nodes))
            same(row['assignment_admissible'], admissible, '原生唯一搭载约束')
            if admissible and row['update_allowed']:
                valid.append(row)
            checks += 11 + len(inputs) + len(choices)
        forecast = d['selected']['forecast']
        if forecast is None:
            same(d['selected']['config'], d['current_config'], '观察不足保持')
        else:
            rank = lambda r: (round(r['predicted_excess'], 12), r['literature_objective'], r['update_rpc_count'],
                              tuple(-1 if x is None else x for x in r['config']))
            # 与原策略config_code相同的序号排序，None为-1不影响顺序。
            best = min(valid, key=rank)
            same(d['selected']['config'], best['config'], '已评估可行候选中文献排序')
            same(forecast, best, '选择评分绑定')
            header = dict.fromkeys(('e1', 'e2', 'e3'), 0)
            _, choices, _, _ = prediction(v, best['config'], options, expected_rates)
            for f, route in ROUTES.items():
                rate = expected_rates.get(f)
                if rate is None or rate <= 0: continue
                spacing = 1e6 / min(rate, options['maximum_rate_pps'])
                t = v['now_us'] + ((f[1] % 31) + .5) / 31 * spacing
                while t < v['now_us'] + options['horizon_us']:
                    for n in route: header[f'e{n}'] += 12
                    t += spacing
            history.append(dict(time_us=d['time_us'], cost=best['cost'], header_bytes=header,
                basis='过去自身选择的在线预测，不是执行器账本'))
        checks += 12
    return checks
