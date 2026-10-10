"""固定阈值与未来已知轨迹的硬期限 MILP，提供参照而非部署策略。"""
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix
from orchestration.model import HEADER_BYTES, RECORD_BYTES, EXPORT_FIXED_BYTES, costs, validate_trace
from orchestration.metrics import evaluate


def solve(tasks, packets, threshold_us, start, end, budgets=None,
          gate_scope='flow', initial_gate=None, force_all=False, time_limit=20):
    validate_trace(tasks, packets, start, end)
    if gate_scope not in ['flow', 'port'] or threshold_us < 0:
        raise ValueError('门控参数不合法')
    n, h = len(packets), len(tasks)
    if budgets is not None and len(budgets) != h + 1:
        raise ValueError('预算须包含每条带内链路和一条导出链路')
    # y[p,j]、导出包 r[p]、任务接纳 z[j]。
    count = n * h + n + h
    report = lambda p: n * h + p
    accept = lambda j: n * h + n + j
    rows, lowers, uppers = [], [], []
    lower, upper = np.zeros(count), np.ones(count)

    def add(coefficients, lo=-np.inf, hi=np.inf):
        rows.append(coefficients)
        lowers.append(lo)
        uppers.append(hi)

    for p, packet in enumerate(packets):
        add({p * h + j: 1 for j in range(h)}, hi=packet.slots())
        add({**{p * h + j: -1 for j in range(h)}, report(p): 1}, hi=0)
        for j, task in enumerate(tasks):
            y = p * h + j
            add({y: 1, accept(j): -1}, hi=0)
            add({y: 1, report(p): -1}, hi=0)
            node_index = packet.path.index(task.node)
            key = (task.node, packet.flow if gate_scope == 'flow' else 0)
            if key in (initial_gate or {}) and packet.gate_us[node_index] - initial_gate[key] < threshold_us:
                upper[y] = 0
    for j, task in enumerate(tasks):
        for p, packet in enumerate(packets):
            index = packet.path.index(task.node)
            for q in range(p + 1, n):
                other = packets[q]
                if gate_scope == 'flow' and packet.flow != other.flow:
                    continue
                if abs(packet.gate_us[index] - other.gate_us[index]) < threshold_us:
                    add({p * h + j: 1, q * h + j: 1}, hi=1)
        checks = sorted({start, end, *(p.arrival_us for p in packets if start < p.arrival_us <= end)})
        for t in checks:
            fresh_initial = task.initial_generated_us is not None and task.initial_generated_us >= t - task.deadline_us
            coefficients = {accept(j): -1}
            for p, packet in enumerate(packets):
                received = packet.arrival_us <= t if t == start else packet.arrival_us < t
                generation = packet.generated_us[packet.path.index(task.node)]
                if received and generation >= t - task.deadline_us:
                    coefficients[p * h + j] = 1
            add(coefficients, lo=-int(fresh_initial))
        if force_all:
            lower[accept(j)] = 1
    coefficients_cost = np.zeros(count)
    inband_fixed = [sum(HEADER_BYTES * p.envelope() for p in packets)] * h
    for edge in range(h):
        coefficients = {}
        for p, packet in enumerate(packets):
            for j, task in enumerate(tasks):
                if packet.path.index(task.node) <= edge:
                    coefficients[p * h + j] = RECORD_BYTES
                    coefficients_cost[p * h + j] += RECORD_BYTES
        if budgets is not None:
            add(coefficients, hi=budgets[edge] - inband_fixed[edge])
    export = {report(p): EXPORT_FIXED_BYTES for p in range(n)}
    export.update({p * h + j: RECORD_BYTES for p in range(n) for j in range(h)})
    for variable, value in export.items():
        coefficients_cost[variable] += value
    if budgets is not None:
        add(export, hi=budgets[-1])

    def constraints():
        rr, cc, values = [], [], []
        for row, coefficients in enumerate(rows):
            for column, value in coefficients.items():
                rr.append(row); cc.append(column); values.append(value)
        matrix = coo_matrix((values, (rr, cc)), shape=(len(rows), count)).tocsc()
        return LinearConstraint(matrix, lowers, uppers)

    objective = np.zeros(count)
    for j, task in enumerate(tasks):
        objective[accept(j)] = -task.weight
    first = milp(objective, integrality=np.ones(count), bounds=Bounds(lower, upper),
                 constraints=constraints(), options={'time_limit': time_limit, 'mip_rel_gap': 0})
    answer = {'status': 'optimal' if first.status == 0 else 'infeasible' if first.status == 2 else 'limited',
              'solver_status': int(first.status), 'message': first.message,
              'threshold_us': threshold_us, 'gate_scope': gate_scope,
              'future_packets_known': True, 'scope': '固定延迟、无丢失、固定阈值及单一预算窗口的离线权限参照'}
    if first.x is None:
        return answer
    best_weight = sum(task.weight * round(first.x[accept(j)]) for j, task in enumerate(tasks))
    solution = first
    if first.status == 0:
        add({accept(j): task.weight for j, task in enumerate(tasks)}, lo=best_weight, hi=best_weight)
        second = milp(coefficients_cost, integrality=np.ones(count), bounds=Bounds(lower, upper),
                      constraints=constraints(), options={'time_limit': time_limit, 'mip_rel_gap': 0})
        answer['cost_solver_status'] = int(second.status)
        if second.x is not None:
            solution = second
    selected = {packet.packet_id: [task.node for j, task in enumerate(tasks) if solution.x[p * h + j] > 0.5] for p, packet in enumerate(packets)}
    accepted = [task.node for j, task in enumerate(tasks) if solution.x[accept(j)] > 0.5]
    cost = costs(packets, selected)
    measured = evaluate(tasks, packets, selected, start, end)
    # 与约束构造独立的连续年龄积分及字节核对。
    assert all(row['violation_fraction'] <= 1e-8 for row in measured['tasks'] if row['node'] in accepted)
    assert all(len(selected[p.packet_id]) <= p.slots() for p in packets)
    for task in tasks:
        events = {}
        for p in packets:
            if task.node in selected[p.packet_id]:
                key = p.flow if gate_scope == 'flow' else 0
                events.setdefault(key, []).append(p.gate_us[p.path.index(task.node)])
        assert all(all(b - a >= threshold_us - 1e-8 for a, b in zip(sorted(ts), sorted(ts)[1:])) for ts in events.values())
    if budgets is not None:
        assert all(actual <= budget for actual, budget in zip(cost['inband_per_link_bytes'] + [cost['export_bytes']], budgets))
    answer.update(admitted_weight=best_weight, accepted_nodes=accepted, selected=selected, cost=cost,
                  metrics=measured, cost_optimal=answer.get('cost_solver_status') == 0,
                  mip_gap=float(first.mip_gap) if hasattr(first, 'mip_gap') else None)
    return answer
