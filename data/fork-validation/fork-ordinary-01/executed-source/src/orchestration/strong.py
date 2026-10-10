"""文献机制的固定路径适配及独立的逐包约束核对。"""
import math

MODULUS = 1 << 48


class FlowFilter:
    def __init__(self, ideal_us, minimum_rate, deviation_limit_us, checks=3, failures=2):
        self.ideal_us, self.minimum_rate, self.deviation_limit_us = ideal_us, minimum_rate, deviation_limit_us
        self.checks, self.failures = checks, failures
        self.state = {}

    def update(self, flow, count, dev, now_ns, initial_ns):
        old = self.state.get(flow, {'count': 0, 'dev': 0, 'time': initial_ns,
                                   'rate_fail': 0, 'dev_fail': 0, 'checks': 0, 'removed': False})
        elapsed = (now_ns - old['time']) / 1e9
        if elapsed <= 0 or count < old['count'] or dev < old['dev']:
            raise ValueError('观察窗口或计数回绕不满足条件')
        rate = (count - old['count']) / elapsed
        deviation = dev - old['dev']
        state = {'count': count, 'dev': dev, 'time': now_ns,
                 'rate_fail': old['rate_fail'] + 1 if rate < self.minimum_rate else 0,
                 'dev_fail': old['dev_fail'] + 1 if deviation >= self.deviation_limit_us else 0,
                 'checks': old['checks'] + 1,
                 'removed': old['removed'] or old['rate_fail'] + 1 >= self.failures and rate < self.minimum_rate
                            or old['dev_fail'] + 1 >= self.failures and deviation >= self.deviation_limit_us}
        self.state[flow] = state
        s = 2e6 / self.ideal_us
        return {'flow': flow, 'rate_pps': rate, 'delta_dev_us': deviation, **state,
                'eligible': not state['removed'] and state['checks'] >= self.checks,
                'ratio_n': max(1, math.floor(rate / s)), 'sampling_margin_alpha': 2}


class BooleanFeedback:
    def __init__(self, thresholds_us, k=2):
        self.grace = {node: k * t * 1000 for node, t in thresholds_us.items()}
        self.forced = {node: False for node in thresholds_us}
        self.lease = dict.fromkeys(thresholds_us, 0)

    def tick(self, now_ns, latest):
        changes = []
        for node, grace in self.grace.items():
            stale = now_ns - latest[node] > grace
            if stale:
                self.lease[node] = now_ns + grace
            value = stale or self.forced[node] and now_ns < self.lease[node]
            if value != self.forced[node]:
                self.forced[node] = value
                changes.append((node, value))
        return changes


def predict(packets, traces, source, fmt, mode, scope, thresholds, capacities, weights):
    gates = [dict() for _ in range(3)]
    source_state = {}
    expected, events = {}, []
    inband = [0, 0, 0]
    counts = [0, 0, 0]
    errors = []
    for p in packets:
        pid, flow, idx = p['packet_id'], p['flow'], p['packet_id'] & 4095
        time = source['trace_source_time'][idx]
        prev_count, prev_time, prev_dev = source_state.get(flow, (0, None, 0))
        dev = prev_dev + (max((time - prev_time) % MODULUS - 100000, 0) if prev_time is not None else 0)
        count = (prev_count + 1) % (1 << 32)
        source_state[flow] = count, time, dev
        if source['trace_source_count'][idx] != count or source['trace_source_dev'][idx] != dev:
            errors.append(f'到包统计不符 {pid}')
        sampled = bool(source['trace_source_active'][idx] and count % source['trace_source_ratio'][idx] == 0)
        if sampled != bool(source['trace_source_sampled'][idx]):
            errors.append(f'源端 1:N 不符 {pid}')
        envelope = sampled and p['ip_length'] + fmt['header'] <= 1500
        length = p['ip_length'] + fmt['header'] * envelope
        selected, seen = [], 0
        for node, trace in enumerate(traces, 1):
            now = trace['trace_arrival'][idx]
            key = 258 if scope == 'port' else flow
            last = gates[node - 1].get(key)
            threshold = thresholds[node - 1]
            force = trace['trace_force'][idx]
            capacity = capacities[node - 1]
            draw = trace['trace_draw'][idx]
            decision, replaced = 5, False
            before = len(selected)
            if sampled and not envelope:
                decision = 4 if node == 1 else 5
            elif envelope:
                if last is not None and not force and (now - last) % MODULUS < threshold:
                    decision = 2
                elif mode and capacity == 1:
                    increment = weights[node - 1] if mode == 2 else 1
                    if not selected and length + fmt['record'] > 1500:
                        decision = 4
                    else:
                        seen += increment
                        if not 1 <= draw <= seen:
                            errors.append(f'随机数超界 {pid}/{node}')
                        if not selected or draw <= increment:
                            decision = 8 if selected else 1
                            replaced = bool(selected)
                        else:
                            decision = 7
                elif len(selected) >= min(capacity, 8):
                    decision = 3
                elif length + fmt['record'] > 1500:
                    decision = 4
                else:
                    decision = 1
            if decision in [1, 8]:
                record = {'switch_id': node, 'hop': node, 'generated_us': trace['trace_generated'][idx],
                          'queue_packets': trace['trace_value'][idx]}
                if replaced: selected[0] = record
                else:
                    selected.insert(0, record)
                    length += fmt['record']
                gates[node - 1][key] = now
                counts[node - 1] += 1
            if trace['trace_decision'][idx] != decision or trace['trace_count_before'][idx] != before:
                errors.append(f'选择决策不符 {pid}/{node}')
            if trace['trace_gate_index'][idx] != key:
                errors.append(f'门控索引不符 {pid}/{node}')
            inband[node - 1] += length - p['ip_length']
            events.append({'packet_id': pid, 'node': node, 'decision': decision,
                           'draw': draw, 'candidate_weight_sum': seen, 'force': force,
                           'gate_us': now, 'count_before': before})
        expected[pid] = {'records': selected, 'envelope': envelope, 'sampled': sampled}
    return {'packets': expected, 'events': events, 'gates': gates, 'source_state': source_state,
            'inband_per_link_bytes': inband, 'successful_insertions_per_node': counts, 'errors': errors}
