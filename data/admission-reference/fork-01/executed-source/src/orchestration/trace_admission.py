"""固定分支轨迹的硬期限接纳上界。逐包选择不是已部署的编排策略。"""
from dataclasses import dataclass
from itertools import combinations
import math

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from orchestration.budget_model import FORMATS, ledger_cost
from orchestration.metrics import age_metrics
from orchestration.model import Task
from orchestration.path_reference import Policy, validate


@dataclass(frozen=True)
class Demand:
    node: int
    weight: int = 1
    initial_generated_us: float | None = None


def slots(packet, fmt):
    return max(0, min(packet.capacity, len(packet.path),
                      (1500 - packet.ip_bytes - fmt.header_bytes) // fmt.record_bytes))


def check_inputs(tasks, packets, phases, fmt, budget=None):
    if not packets:
        raise ValueError('轨迹不能为空')
    keys = {(n, p.flow): 1 for p in packets for n in p.path}
    validate(packets, phases, [(packets[0].gate_us[0], Policy(keys))])
    if not tasks or len({t.node for t in tasks}) != len(tasks):
        raise ValueError('任务不能为空或重复')
    for t in tasks:
        if t.node not in (1, 2, 3) or not isinstance(t.weight, int) or t.weight <= 0:
            raise ValueError('任务编号或正整数权重不合法')
        if t.initial_generated_us is not None and (not math.isfinite(t.initial_generated_us)
                                                    or t.initial_generated_us > phases[0].start_us):
            raise ValueError('窗口开始前已收到的初始样本不合法')
    if fmt.header_bytes <= 0 or fmt.record_bytes <= 0 or fmt.export_minimum_bytes < 0:
        raise ValueError('格式宽度不合法')
    if budget:
        values = budget.inband_per_link_bytes + (budget.export_bytes,) + budget.control_per_link_bytes
        if len(budget.inband_per_link_bytes) != 3 or not budget.control_per_link_bytes or any(
                not isinstance(v, int) or v < 0 for v in values):
            raise ValueError('须分别给定三条数据链路及非负整数预算')


def coverage_rows(tasks, packets, phases):
    """各区间右端检查收报前的左极限，窗口起点检查已收到的样本。"""
    rows = []
    for k, phase in enumerate(phases):
        moments = sorted({phase.start_us, phase.end_us, *(p.arrival_us for p in packets
                          if p.arrival_us is not None and phase.start_us < p.arrival_us < phase.end_us)})
        for moment in moments:
            at_start = moment == phase.start_us
            for j, task in enumerate(tasks):
                deadline = phase.deadlines_us[task.node - 1]
                candidates = []
                for i, p in enumerate(packets):
                    if task.node not in p.path or p.arrival_us is None:
                        continue
                    received = p.arrival_us <= moment if at_start else p.arrival_us < moment
                    if received and p.generated_us[p.path.index(task.node)] >= moment - deadline:
                        candidates.append(i)
                initial = task.initial_generated_us is not None and task.initial_generated_us >= moment - deadline
                rows.append({'phase': k, 'moment_us': moment, 'side': 'at' if at_start else 'left',
                             'task_index': j, 'node': task.node, 'deadline_us': deadline,
                             'candidates': candidates, 'initial_fresh': initial})
    return rows


def certificates(tasks, packets, phases, fmt=FORMATS['prefix']):
    """必要条件证书。容量割只检查同一时刻，未通过时可拒绝，通过时不可保证。"""
    check_inputs(tasks, packets, phases, fmt)
    rows = coverage_rows(tasks, packets, phases)
    gaps, capacity_cuts = [], []
    groups = {}
    for row in rows:
        groups.setdefault((row['phase'], row['moment_us'], row['side']), []).append(row)
        if row['initial_fresh']:
            continue
        feasible = [i for i in row['candidates'] if slots(packets[i], fmt)]
        if not feasible:
            gaps.append({**{k: row[k] for k in ('phase', 'moment_us', 'side', 'node', 'deadline_us')},
                         'kind': 'opportunity_gap' if not row['candidates'] else 'mtu_gap',
                         'raw_fresh_packet_ids': [packets[i].packet_id for i in row['candidates']]})
    for (phase, moment, side), group in groups.items():
        needed = [row for row in group if not row['initial_fresh']]
        for size in range(2, len(needed) + 1):
            for subset in combinations(needed, size):
                # 一个包对同一任务至多贡献一次，故取 min(K, 合格任务数)。
                union = sorted({i for row in subset for i in row['candidates']})
                capacities = [{'packet_id': packets[i].packet_id,
                               'usable_slots': min(slots(packets[i], fmt),
                                                   sum(i in row['candidates'] for row in subset))} for i in union]
                available = sum(p['usable_slots'] for p in capacities)
                if available < size:
                    capacity_cuts.append({'kind': 'capacity_cut', 'phase': phase, 'moment_us': moment,
                                          'side': side, 'nodes': [r['node'] for r in subset],
                                          'required_slots': size, 'available_slots': available,
                                          'packets': capacities})
    return {'gaps': gaps, 'capacity_cuts': capacity_cuts,
            'necessary_checks_passed': not gaps and not capacity_cuts,
            'sufficient_for_joint_feasibility': False}


def selection_cost(packets, selected, source_selected, fmt):
    wire, export = [0, 0, 0], 0
    for p in packets:
        chosen = set(selected.get(p.packet_id, []))
        active = source_selected[p.packet_id]
        if chosen - set(p.path) or len(chosen) > slots(p, fmt) or chosen and not active:
            raise ValueError('选择超出实际资格、容量或未封装')
        if active and p.ip_bytes + fmt.header_bytes > 1500:
            raise ValueError('源端封装超过 MTU')
        for i, edge in enumerate(p.links):
            wire[edge] += fmt.header_bytes * active + fmt.record_bytes * len(chosen.intersection(p.path[:i + 1]))
        if chosen and p.export_emitted:
            export += fmt.export_bytes(len(chosen))
    return {'inband_per_link_bytes': wire, 'export_bytes': export,
            'data_total_byte_hops': sum(wire) + export}


def selection_metrics(tasks, packets, phases, selected):
    samples = {t.node: [] for t in tasks}
    for p in packets:
        if p.arrival_us is not None:
            for i, node in enumerate(p.path):
                if node in samples and node in selected.get(p.packet_id, []):
                    samples[node].append((p.generated_us[i], p.arrival_us))
    return [{'phase': k, 'tasks': [age_metrics(Task(t.node, phase.deadlines_us[t.node - 1],
                                                 initial_generated_us=t.initial_generated_us),
                                            samples[t.node], phase.start_us, phase.end_us) for t in tasks]}
            for k, phase in enumerate(phases)]


def solve(tasks, packets, phases, budget=None, fmt=FORMATS['prefix'], thresholds=None,
          initial_gate=None, gate_scope='flow', source_mode='fixed', control_events=(),
          force_all=False, time_limit=20):
    """先最大化整段轨迹都履约的任务权重，再最小化数据费用。未来事件完全已知。"""
    check_inputs(tasks, packets, phases, fmt, budget)
    if source_mode not in ('fixed', 'optional') or gate_scope not in ('flow', 'port'):
        raise ValueError('封装或门控模式不合法')
    thresholds, initial_gate = thresholds or {}, initial_gate or {}
    valid_keys = {(t.node, p.flow if gate_scope == 'flow' else 0) for t in tasks for p in packets if t.node in p.path}
    if set(thresholds) - valid_keys or set(initial_gate) - valid_keys or any(
            not math.isfinite(v) or v < 0 for v in thresholds.values()):
        raise ValueError('门控索引或阈值不合法')
    for key, t in initial_gate.items():
        earliest = min(p.gate_us[p.path.index(key[0])] for p in packets
                       if key[0] in p.path and (gate_scope == 'port' or p.flow == key[1]))
        if not math.isfinite(t) or t > earliest:
            raise ValueError('初始成功门控须早于已声明的全部机会')
    control_events = tuple(control_events)
    control = ledger_cost(control_events, len(budget.control_per_link_bytes) if budget else 1)
    answer = {'future_packets_known': True, 'deployment_feasibility_established': False,
              'format': fmt.name, 'source_mode': source_mode, 'gate_scope': gate_scope,
              'control_cost': control, 'cost_optimal': False,
              'scope': '固定分支、无替换、逐包自由选择的离线接纳上界；不优化观察、下发或未来预测'}
    if budget and any(v > b for v, b in zip(control['control_per_link_bytes'], budget.control_per_link_bytes)):
        return {**answer, 'status': 'infeasible', 'reason': 'fixed_control_budget'}
    n, h = len(packets), len(tasks)
    source = lambda i: n * h + i
    report = lambda i: n * h + n + i
    exported = lambda i: n * h + 2 * n + i
    accept = lambda j: n * h + 3 * n + j
    count = n * h + 3 * n + h
    lo, hi = np.zeros(count), np.ones(count)
    rows, lower, upper = [], [], []
    objective_cost = np.zeros(count)

    def add(coeff, low=-np.inf, high=np.inf):
        rows.append(coeff)
        lower.append(low)
        upper.append(high)

    maximum_export = fmt.export_bytes(h)
    for i, p in enumerate(packets):
        hi[source(i)] = int(p.ip_bytes + fmt.header_bytes <= 1500)
        if source_mode == 'fixed':
            lo[source(i)] = hi[source(i)]
        hi[exported(i)] = maximum_export
        ys = {i * h + j: 1 for j in range(h)}
        add(ys, high=slots(p, fmt))
        add({report(i): 1, **{k: -v for k, v in ys.items()}}, high=0)
        for j, task in enumerate(tasks):
            y = i * h + j
            add({y: 1, source(i): -1}, high=0)
            add({y: 1, report(i): -1}, high=0)
            add({y: 1, accept(j): -1}, high=0)
            if task.node not in p.path:
                hi[y] = 0
                continue
            key = (task.node, p.flow if gate_scope == 'flow' else 0)
            if key in initial_gate and p.gate_us[p.path.index(task.node)] - initial_gate[key] < thresholds.get(key, 0):
                hi[y] = 0
        emitted = int(p.export_emitted)
        add({exported(i): 1, report(i): -(34 + fmt.header_bytes) * emitted,
             **{k: -fmt.record_bytes * emitted for k in ys}}, low=0)
        add({exported(i): 1, report(i): -fmt.export_minimum_bytes * emitted}, low=0)
        add({exported(i): 1, report(i): -maximum_export * emitted}, high=0)
        objective_cost[source(i)] = fmt.header_bytes * len(p.links)
        objective_cost[exported(i)] = 1
    for j, task in enumerate(tasks):
        relevant = [(i, p) for i, p in enumerate(packets) if task.node in p.path]
        for (i, p), (k, q) in combinations(relevant, 2):
            if gate_scope == 'flow' and p.flow != q.flow:
                continue
            key = task.node, p.flow if gate_scope == 'flow' else 0
            if abs(p.gate_us[p.path.index(task.node)] - q.gate_us[q.path.index(task.node)]) < thresholds.get(key, 0):
                add({i * h + j: 1, k * h + j: 1}, high=1)
        if force_all:
            lo[accept(j)] = 1
    seen = set()
    for row in coverage_rows(tasks, packets, phases):
        signature = row['task_index'], tuple(row['candidates']), row['initial_fresh']
        if signature in seen:
            continue
        seen.add(signature)
        add({accept(row['task_index']): -1,
             **{i * h + row['task_index']: 1 for i in row['candidates']}}, low=-int(row['initial_fresh']))
    for edge in range(3):
        coeff = {}
        for i, p in enumerate(packets):
            if edge not in p.links:
                continue
            coeff[source(i)] = fmt.header_bytes
            edge_index = p.links.index(edge)
            for j, task in enumerate(tasks):
                if task.node in p.path[:edge_index + 1]:
                    coeff[i * h + j] = fmt.record_bytes
                    objective_cost[i * h + j] += fmt.record_bytes
        if budget:
            add(coeff, high=budget.inband_per_link_bytes[edge])
    if budget:
        add({exported(i): 1 for i in range(n)}, high=budget.export_bytes)

    def optimize(objective):
        rr, cc, values = [], [], []
        for r, coeff in enumerate(rows):
            for c, v in coeff.items():
                rr.append(r)
                cc.append(c)
                values.append(v)
        matrix = coo_matrix((values, (rr, cc)), shape=(len(rows), count)).tocsc()
        return milp(objective, integrality=np.ones(count), bounds=Bounds(lo, hi),
                    constraints=LinearConstraint(matrix, lower, upper),
                    options={'time_limit': time_limit, 'mip_rel_gap': 0})

    objective = np.zeros(count)
    for j, task in enumerate(tasks):
        objective[accept(j)] = -task.weight
    first = optimize(objective)
    answer.update(status='optimal' if first.status == 0 else 'infeasible' if first.status == 2 else 'limited',
                  solver_status=int(first.status), message=first.message)
    if first.x is None:
        return answer
    best_weight = sum(task.weight * round(first.x[accept(j)]) for j, task in enumerate(tasks))
    solution = first
    if first.status == 0:
        add({accept(j): task.weight for j, task in enumerate(tasks)}, low=best_weight, high=best_weight)
        second = optimize(objective_cost)
        answer['cost_solver_status'] = int(second.status)
        if second.x is not None:
            solution = second
        answer['cost_optimal'] = second.status == 0
    selected = {p.packet_id: [t.node for j, t in enumerate(tasks) if solution.x[i * h + j] > .5]
                for i, p in enumerate(packets)}
    enabled = {p.packet_id: bool(solution.x[source(i)] > .5) for i, p in enumerate(packets)}
    accepted = [task.node for j, task in enumerate(tasks) if solution.x[accept(j)] > .5]
    actual = selection_cost(packets, selected, enabled, fmt)
    metrics = selection_metrics(tasks, packets, phases, selected)
    assert all(row['violation_fraction'] <= 1e-8 for phase in metrics for row in phase['tasks'] if row['node'] in accepted)
    for j, task in enumerate(tasks):
        groups = {}
        for p in packets:
            if task.node in selected[p.packet_id]:
                key = task.node, p.flow if gate_scope == 'flow' else 0
                groups.setdefault(key, []).append(p.gate_us[p.path.index(task.node)])
        for key, times in groups.items():
            ordered = sorted(times)
            if key in initial_gate:
                ordered = [initial_gate[key]] + ordered
            assert all(b - a >= thresholds.get(key, 0) - 1e-8 for a, b in zip(ordered, ordered[1:]))
    if budget:
        assert all(v <= b for v, b in zip(actual['inband_per_link_bytes'], budget.inband_per_link_bytes))
        assert actual['export_bytes'] <= budget.export_bytes
    answer.update(admitted_weight=best_weight, accepted_nodes=accepted, selected=selected,
                  source_selected=enabled, cost=actual, metrics=metrics)
    return answer
