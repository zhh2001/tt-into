"""三个固定对象的因果时隙适配，固定生效延迟与空隙是模型前提。"""
from dataclasses import replace
import math

from orchestration.assignment_reference import Dependency, Flow, Item, expired, solve_dinto, solve_intopp
from orchestration.deployment import KEYS, transition
from orchestration.path_reference import Policy, replay, validate, violations

WEIGHTS = (2, 2, 1)
START, END, SLOT_US, EFFECT_DELAY_US = -800000, 2400000, 100000, 2000


def decision_times(phases):
    return [START - EFFECT_DELAY_US] + sorted(set(range(START + SLOT_US, END, SLOT_US)) |
                                             {int(p.start_us) for p in phases[1:]})


def current_deadlines(now, phases):
    # 预热只知道第一阶段需求，后续需求在其开始时才可见。
    return next(p.deadlines_us for p in phases if p.start_us <= max(now, 0) < p.end_us)


def tt_threshold(rule, deadline):
    if rule == 'off':
        return None
    if rule == 'always':
        return 1
    if rule == 'half':
        return max(1, int(deadline / 2))
    if rule == 'deadline':
        return int(deadline)
    if rule == 'margin':
        # 固定 100 ms 名义周期和 15 ms 预留，不从测试轨迹估计未来间隔。
        return max(1, math.floor((deadline - 15000) / 100000) * 100000 - 50000)
    raise ValueError('未知 TT 阈值规则')


def tt_policy(parameters, deadlines):
    a, b, shared = parameters['branch1'], parameters['branch2'], parameters['shared']
    thresholds = {(1, 1): tt_threshold(a, deadlines[0]), (2, 2): tt_threshold(b, deadlines[1])}
    for flow in (1, 2, 3):
        if shared.startswith('flow'):
            thresholds[3, flow] = 1 if flow == int(shared[-1]) else None
        else:
            thresholds[3, flow] = tt_threshold(shared, deadlines[2])
    return Policy(thresholds)


def _observations(now, past, schedule):
    samples = replay(past, schedule)['samples'] if past else {n: [] for n in (1, 2, 3)}
    return {n: max((g for g, arrival in samples[n] if arrival <= now), default=None) for n in samples}


def run_slot_reference(packets, phases, family, parameters, capacity, rpc_cost, budget, detailed=False):
    """执行者仅向决策器提供已经送达的报告，保留成功门控与历史指派。

    rpc_cost 来自既有计量。路由 bootstrap、TCP 帧和主机进程不在此 API 层口径内。
    预算只评价整次执行，不事后删包，也不宣称已实现在线硬预算监管。
    """
    if capacity not in (1, 2) or family not in ('tt', 'dinto', 'intopp', 'intopp_ahead'):
        raise ValueError('不支持的容量或参照策略')
    packets = [replace(p, capacity=capacity) for p in packets]
    flows = [Flow(1, (1, 3), capacity * 12), Flow(2, (2, 3), capacity * 12), Flow(3, (3,), capacity * 12)]
    schedule, decisions, previous, queue = [], [], {}, 0.
    update_calls, update_bytes, executed_decisions = 0, 0, 0
    variation = 0
    previous_deadlines = None
    for now in decision_times(phases):
        deadlines = current_deadlines(now, phases)
        # 到包记录仅由执行者用于重放，决策器不接收未送达报告或未来包。
        latest = _observations(now, [p for p in packets if p.gate_us[0] < now], schedule) if family != 'tt' else {}
        age = {n: None if latest.get(n) is None else now - latest[n] for n in (1, 2, 3)}
        if family == 'tt':
            policy = tt_policy(parameters, deadlines)
            assignment_result = None
            if previous_deadlines == deadlines:
                continue
        else:
            gains = [float(w) for w in WEIGHTS]
            if family == 'dinto' and parameters['gain'] == 'age':
                gains = [w * (1 + (3 if age[n] is None else min(3., age[n] / deadlines[n - 1])))
                         for n, w in enumerate(WEIGHTS, 1)]
            items = [Item(n, 1, 12, gains[n - 1]) for n in (1, 2, 3)]
            if family == 'dinto':
                assignment_result = solve_dinto(items, flows, previous, queue,
                    parameters['tradeoff'], parameters['average_change_bytes'], method='enumeration')
                queue = assignment_result['next_queue']
            else:
                ahead = parameters['lookahead_us'] if family == 'intopp_ahead' else 0
                # 一对象一种状态包：三个独立应用，不把三对象误写成全复现的聚类任务。
                deps = [Dependency(f'task-{n}', (n,), (1,), False,
                                   active=expired(age[n], deadlines[n - 1], ahead), weight=w)
                        for n, w in enumerate(WEIGHTS, 1)]
                assignment_result = solve_intopp(items, flows, deps, previous, temporal='distinct', method='enumeration')
            previous = assignment_result['assignment']
            variation += assignment_result['variation_bytes']
            policy = Policy({key: 1 if previous.get((key[0], 1)) == key[1] else None for key in KEYS})
        executed_decisions += 1
        old = schedule[-1][1].thresholds if schedule else dict.fromkeys(KEYS, None)
        if not schedule:
            # 五个初始阈值已列入共同安装账本，这里只增加初次启用 max_count。
            writes = [{'node': n, 'flow': f, 'register': 'TTEgress.max_count', 'value': capacity}
                      for (n, f), value in sorted(policy.thresholds.items()) if value is not None]
        else:
            writes = [vars(w) for w in transition(old, policy.thresholds, capacity)]
        amount = sum(rpc_cost['write_bytes'][w['register']] for w in writes)
        update_calls += len(writes)
        update_bytes += amount
        effective = now + EFFECT_DELAY_US
        if not schedule or old != policy.thresholds:
            schedule.append((effective, policy))
        if detailed:
            decisions.append({'issued_us': now, 'effective_us': effective, 'deadlines_us': deadlines,
                              'latest_received_generated_us': latest, 'queue_after': queue,
                              'assignment': [{'node': n, 'kind': k, 'flow': f} for (n, k), f in sorted(previous.items())],
                              'thresholds': [{'node': n, 'flow': f, 'threshold_us': v} for (n, f), v in sorted(policy.thresholds.items())],
                              'writes': writes, 'update_serialized_bytes': amount})
        previous_deadlines = deadlines
    validate(packets, phases, schedule)
    result = replay(packets, schedule)
    bad = violations(result['samples'], phases)
    fulfilled = [n for n in (1, 2, 3) if all(row[n - 1] <= 1e-10 for row in bad)]
    feasible = (all(v <= b for v, b in zip(result['inband_per_link_bytes'], budget['inband_per_link_bytes'])) and
                result['export_bytes'] <= budget['export_bytes'] and
                rpc_cost['installation_bytes'] + update_bytes <= budget['runtime_api_serialized_bytes'])
    weighted_bad = sum((phase.end_us - phase.start_us) * sum(w * v for w, v in zip(WEIGHTS, row))
                       for phase, row in zip(phases, bad)) / (5 * END)
    output = {'fulfilled_nodes': fulfilled, 'fulfilled_weight': sum(WEIGHTS[n - 1] for n in fulfilled),
              'budget_satisfied': feasible, 'budget_qualified_weight': sum(WEIGHTS[n - 1] for n in fulfilled) if feasible else 0,
              'violation_fractions_by_phase': bad, 'weighted_violation_fraction': weighted_bad,
              'inband_per_link_bytes': result['inband_per_link_bytes'], 'export_bytes': result['export_bytes'],
              'data_total_byte_hops': result['data_total_byte_hops'], 'assignment_variation_bytes': variation,
              'runtime_api_installation_serialized_bytes': rpc_cost['installation_bytes'],
              'runtime_api_update_serialized_bytes': update_bytes, 'runtime_api_update_calls': update_calls,
              'runtime_api_total_serialized_bytes': rpc_cost['installation_bytes'] + update_bytes,
              'extra_polling_observation_bytes': 0, 'configured_changes': len(schedule), 'slot_decisions': executed_decisions}
    if detailed:
        output.update(decisions=decisions, selected=result['selected'], samples=result['samples'],
                      schedule=[{'effective_us': t, 'thresholds': [{'node': n, 'flow': f, 'threshold_us': v}
                                for (n, f), v in sorted(p.thresholds.items())]} for t, p in schedule])
    return output
