"""共享对象活跃区间的连续时效损失及分层费用核对。"""
import math
from types import SimpleNamespace

from orchestration.metrics import age_metrics
from orchestration.event_schema import integer


def _clipped_area(samples, start, end, target, clip):
    latest, cursor, area = None, start, 0.0
    for generated, arrived in sorted(samples, key=lambda row: (row[1], row[0])):
        if arrived <= start:
            latest = generated if latest is None else max(latest, generated)
            continue
        if arrived >= end:
            break
        area += integrate_clipped(cursor, arrived, latest, target, clip)
        cursor = arrived
        latest = generated if latest is None else max(latest, generated)
    return area + integrate_clipped(cursor, end, latest, target, clip)


def integrate_clipped(left, right, generated, target, clip):
    if generated is None:
        return clip * (right - left)
    split = min(right, max(left, generated + target * clip))
    return ((left - generated) * (split - left) + (split - left)**2 / 2) / target + clip * (right - split)


def evaluate_tasks(tasks, receipts, clip=4.0):
    if not tasks or not isinstance(clip, (int, float)) or isinstance(clip, bool) or not math.isfinite(clip) or clip <= 0:
        raise ValueError('需要非空任务与有限正截断值')
    if len({t.task_id for t in tasks}) != len(tasks):
        raise ValueError('任务标识重复')
    for i, first in enumerate(tasks):
        for second in tasks[i + 1:]:
            if first.object_id == second.object_id and max(first.start_us, second.start_us) < min(first.end_us, second.end_us):
                raise ValueError('共享对象的重叠需求应先合并，不能重复计收益')
    rows, weighted_area, weighted_duration, covered_weight = [], 0.0, 0.0, 0.0
    for task in tasks:
        relevant = [r for r in receipts if r['object_id'] == task.object_id]
        samples = [(r['generated_us'], r['received_us']) for r in relevant]
        for generated, received in samples:
            integer(generated, '生成真值')
            integer(received, '收报时间')
            if generated > received:
                raise ValueError('收到未来生成样本')
        age = age_metrics(SimpleNamespace(node=task.object_id.label, initial_generated_us=None,
                                         deadline_us=task.target_us, epsilon=0.0),
                          samples, task.start_us, task.end_us)
        effective = [(r['received_us'], r['received_us']) for r in relevant if r['accepted']]
        recency = age_metrics(SimpleNamespace(node=task.object_id.label, initial_generated_us=None,
                                             deadline_us=task.target_us, epsilon=0.0),
                              effective, task.start_us, task.end_us)
        area = _clipped_area(samples, task.start_us, task.end_us, task.target_us, clip)
        duration = task.end_us - task.start_us
        prior = any(arrived <= task.start_us for _, arrived in samples)
        first = min((a for _, a in samples if task.start_us < a < task.end_us), default=None)
        covered = any(task.start_us <= arrived < task.end_us for _, arrived in effective)
        rows.append({'task_id': task.task_id, 'object_id': task.object_id.label,
                     'active_interval_us': [task.start_us, task.end_us], 'weight': task.weight,
                     'target_us': task.target_us, 'clipped_normalized_loss': area / duration,
                     'generation_age': age, 'effective_receipt_recency': recency,
                     'first_effective_report_wait_us': 0 if prior else None if first is None else first - task.start_us,
                     'active_interval_effective_coverage': covered})
        weighted_area += task.weight * area
        weighted_duration += task.weight * duration
        covered_weight += task.weight * covered
    return {'clip': clip, 'task_count': len(tasks), 'weighted_active_duration_us': weighted_duration,
            'weighted_clipped_normalized_loss': weighted_area / weighted_duration,
            'weighted_active_interval_coverage': covered_weight / sum(t.weight for t in tasks),
            'tasks': rows}


def window_costs(cost_events, start_us, end_us, link_budgets_bps=None, export_budget_bps=None):
    integer(start_us, '费用窗口开始')
    integer(end_us, '费用窗口结束')
    if end_us <= start_us:
        raise ValueError('费用窗口须为正')
    budgets = {} if link_budgets_bps is None else dict(link_budgets_bps)
    values = list(budgets.values()) + ([] if export_budget_bps is None else [export_budget_bps])
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('预算须为有限非负 byte/s')
    seconds = (end_us - start_us) / 1_000_000
    links = dict.fromkeys(sorted({e['link'] for e in cost_events if e['resource'] == 'inband'} | set(budgets)), 0)
    export, control = 0, dict.fromkeys(('installation', 'observation', 'update'), 0)
    calls, entries = dict.fromkeys(control, 0), set()
    for event in cost_events:
        integer(event['time_us'], '费用事件时间')
        integer(event['bytes'], '费用字节', 0)
        expected_basis = {'inband': 'ipv4_increment', 'export': 'ethernet_without_fcs_with_padding',
                          'control': 'serialized_rpc'}
        if event['resource'] not in expected_basis or event.get('basis') != expected_basis[event['resource']]:
            raise ValueError('费用口径错误，控制 API 字节与帧费用不能混用')
        if not start_us <= event['time_us'] < end_us:
            continue
        if event['resource'] == 'inband':
            links[event['link']] += event['bytes']
        elif event['resource'] == 'export':
            export += event['bytes']
        elif event['resource'] == 'control':
            control[event['kind']] += event['bytes']
            if event.get('part') == 'request':
                calls[event['kind']] += 1
                if event['kind'] == 'update':
                    entries.add((event['node'], event['flow']))
    rates = {link: value / seconds for link, value in links.items()}
    return {'window_us': [start_us, end_us], 'inband_per_link_bytes': links,
            'inband_per_link_bytes_per_s': rates, 'export_attempt_bytes': export,
            'export_attempt_bytes_per_s': export / seconds, 'control_serialized_bytes_by_kind': control,
            'control_request_count_by_kind': calls, 'updated_entry_count': len(entries),
            'inband_over_budget_bytes_per_s': {link: max(0.0, rates[link] - budget) for link, budget in budgets.items()},
            'export_over_budget_bytes_per_s': None if export_budget_bps is None else max(0.0, export / seconds - export_budget_bps),
            'budget_contract': '实测窗口平均 byte/s；不把 API 字节与数据帧相加'}
