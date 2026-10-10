"""连续时间年龄积分，旧报告不回退缓存，未知状态计入违约。"""
def age_metrics(task, samples, start, end):
    events = sorted(samples, key=lambda row: (row[1], row[0]))
    latest = task.initial_generated_us
    for generated, arrived in events:
        if arrived <= start and (latest is None or generated > latest):
            latest = generated
    cursor, unknown, violation, area = start, 0.0, 0.0, 0.0
    pieces = []
    updates = []

    def integrate(right):
        nonlocal cursor, unknown, violation, area
        width = right - cursor
        if width <= 0:
            return
        if latest is None:
            unknown += width
            violation += width
        else:
            left_age = cursor - latest
            pieces.append((left_age, width))
            area += left_age * width + width * width / 2
            violation += min(width, max(0.0, right - max(cursor, latest + task.deadline_us)))
        cursor = right

    for generated, arrived in events:
        if not start < arrived < end:
            continue
        integrate(arrived)
        if latest is None or generated > latest:
            latest = generated
            updates.append(arrived)
    integrate(end)
    duration = end - start

    def quantile(probability):
        target = duration * probability
        if target > duration - unknown + 1e-9:
            return None  # JSON 的 null 表示含未知状态而无有限分位值。
        if not pieces:
            return None
        lo, hi = 0.0, max(age + width for age, width in pieces)
        for _ in range(60):
            middle = (lo + hi) / 2
            mass = sum(min(width, max(0.0, middle - age)) for age, width in pieces)
            if mass >= target:
                hi = middle
            else:
                lo = middle
        return hi

    points = [start] + updates + [end]
    return {'node': task.node, 'violation_fraction': violation / duration,
            'unknown_fraction': unknown / duration,
            'mean_age_us': area / duration if unknown == 0 else None,
            'mean_known_age_us': area / (duration - unknown) if duration > unknown else None,
            'p95_age_us': quantile(0.95), 'p99_age_us': quantile(0.99),
            'longest_no_effective_update_us': max(b - a for a, b in zip(points, points[1:])),
            'first_report_wait_us': updates[0] - start if updates else None,
            'fulfilled': violation / duration <= task.epsilon + 1e-10}


def evaluate(tasks, packets, selected, start, end):
    samples = {task.node: [] for task in tasks}
    for packet in packets:
        for index, node in enumerate(packet.path):
            if node in selected.get(packet.packet_id, []):
                samples[node].append((packet.generated_us[index], packet.arrival_us))
    rows = [age_metrics(task, samples[task.node], start, end) for task in tasks]
    weights = sum(task.weight for task in tasks)
    return {'tasks': rows, 'weighted_violation_fraction': sum(task.weight * row['violation_fraction'] for task, row in zip(tasks, rows)) / weights,
            'fulfilled_task_count': sum(row['fulfilled'] for row in rows),
            'task_count': len(tasks)}
