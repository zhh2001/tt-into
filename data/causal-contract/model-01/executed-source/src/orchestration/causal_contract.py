"""有限搭载机会的接纳核对。穷举信息边界，不表示可部署控制器。"""
from dataclasses import dataclass
from itertools import combinations


def _integer(value, name, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError(name + ' 必须为范围内整数')


def age_certificate(deadline_us, initial_generated_us, samples, start_us, end_us):
    """整数微秒下的连续年龄违约时长，缓存只接受更晚的生成时间。"""
    for value, name in [(deadline_us, '期限'), (start_us, '开始'), (end_us, '结束')]:
        _integer(value, name)
    if deadline_us <= 0 or end_us <= start_us:
        raise ValueError('期限与窗口必须为正')
    if initial_generated_us is not None:
        _integer(initial_generated_us, '初始生成时间')
        if initial_generated_us > start_us:
            raise ValueError('初始状态不能来自未来')
    events = []
    for generated, arrived in samples:
        _integer(generated, '生成时间')
        _integer(arrived, '可用时间')
        if generated > arrived:
            raise ValueError('报告不能先于生成可用')
        events.append((generated, arrived))
    latest = initial_generated_us
    events.sort(key=lambda row: (row[1], row[0]))
    for generated, arrived in events:
        if arrived <= start_us and (latest is None or generated > latest):
            latest = generated
    cursor, violation, unknown, peak = start_us, 0, 0, 0

    def integrate(right):
        nonlocal cursor, violation, unknown, peak
        if right <= cursor:
            return
        if latest is None:
            unknown += right - cursor
            violation += right - cursor
        else:
            peak = max(peak, right - latest)
            violation += max(0, right - max(cursor, latest + deadline_us))
        cursor = right

    for generated, arrived in events:
        if start_us < arrived < end_us:
            integrate(arrived)
            if latest is None or generated > latest:
                latest = generated
    integrate(end_us)
    return {'violation_us': violation, 'unknown_us': unknown,
            'peak_age_us': None if unknown else peak, 'fulfilled': violation == 0}


@dataclass(frozen=True)
class Opportunity:
    generated_us: int
    eligible: tuple


def subsets(values, capacity):
    return [group for size in range(min(len(values), capacity) + 1)
            for group in combinations(sorted(values), size)]


def two_opportunity_frontier(tasks, initial, deadlines, first, futures,
                             capacity, record_budget, report_delay_us,
                             start_us, end_us, branch_hint=False):
    """第一次必须统一动作，第二次观察当前资格后再行动。

    tasks 为唯一整数对象标识，futures 为第二次机会的允许分支。
    每次都可以自由选择当前合格记录，这是对部署能力的有利放宽。
    branch_hint=True 表示第一次之前已经获知分支，未模拟获取过程。
    报告延迟固定且已知，无丢失。预算是总记录数，不是字节成本。
    """
    if type(branch_hint) is not bool:
        raise ValueError('分支提示开关必须为布尔值')
    for value, name in [(capacity, '容量'), (record_budget, '记录预算'),
                        (report_delay_us, '报告延迟')]:
        _integer(value, name, 0)
    _integer(start_us, '开始')
    _integer(end_us, '结束')
    if end_us <= start_us or not tasks or not futures:
        raise ValueError('需要非空任务、分支及有效窗口')
    if len(set(tasks)) != len(tasks) or any(type(t) is not int for t in tasks):
        raise ValueError('任务标识必须是互异整数')
    if set(initial) != set(tasks) or set(deadlines) != set(tasks):
        raise ValueError('初态和期限必须覆盖全部任务')
    for task in tasks:
        age_certificate(deadlines[task], initial[task], [], start_us, end_us)
    for opportunity in [first] + list(futures):
        _integer(opportunity.generated_us, '机会时间')
        if not start_us <= opportunity.generated_us < end_us:
            raise ValueError('机会须在窗口内')
        if len(set(opportunity.eligible)) != len(opportunity.eligible):
            raise ValueError('资格不能重复')
        if not set(opportunity.eligible) <= set(tasks):
            raise ValueError('未知资格对象')
    if any(second.generated_us <= first.generated_us for second in futures):
        raise ValueError('第二次机会必须晚于第一次')

    def plans(admitted, second, common_action=None):
        first_actions = [common_action] if common_action is not None else subsets(
            set(first.eligible) & set(admitted), capacity)
        found = []
        for action0 in first_actions:
            for action1 in subsets(set(second.eligible) & set(admitted), capacity):
                if len(action0) + len(action1) > record_budget:
                    continue
                certificates = {}
                for task in admitted:
                    samples = [(p.generated_us, p.generated_us + report_delay_us)
                               for p, action in [(first, action0), (second, action1)]
                               if task in action]
                    certificates[task] = age_certificate(
                        deadlines[task], initial[task], samples, start_us, end_us)
                if all(row['fulfilled'] for row in certificates.values()):
                    found.append({'first': list(action0), 'second': list(action1),
                                  'records': len(action0) + len(action1),
                                  'report_attempts': int(bool(action0)) + int(bool(action1)),
                                  'tasks': certificates})
        # 优先少记录；只有非空动作导出，未建模固定封装费用。
        return sorted(found, key=lambda row: (row['records'], row['report_attempts'],
                                             row['first'], row['second']))

    admitted_sets = sorted(subsets(tasks, len(tasks)), key=lambda x: (-len(x), x))
    offline = []
    for second in futures:
        for admitted in admitted_sets:
            candidates = plans(admitted, second)
            if candidates:
                offline.append({'admitted': list(admitted), 'plan': candidates[0]})
                break
    for admitted in admitted_sets:
        if branch_hint:
            choices = [plans(admitted, second) for second in futures]
            if all(choices):
                causal = {'admitted': list(admitted),
                          'plans': [rows[0] for rows in choices]}
                break
        else:
            for action0 in subsets(set(first.eligible) & set(admitted), capacity):
                choices = [plans(admitted, second, action0) for second in futures]
                if all(choices):
                    causal = {'admitted': list(admitted),
                              'plans': [rows[0] for rows in choices]}
                    break
            else:
                continue
            break
    return {'offline': offline, 'causal': causal,
            'offline_min_admitted': min(len(row['admitted']) for row in offline),
            'causal_admitted': len(causal['admitted']),
            'branch_hint': branch_hint}


def deployment_lead_certificate(packet_times, notice_us, install_delay_us,
                                report_delay_us, initial_generated_us,
                                deadline_us, change_us, end_us):
    """单任务授权于 notice + install 生效，之后每包采样。

    生效之前旧配置没有新的合格报告，初始缓存一直可用。
    核对新报告前的违约下界及完整窗口，未模拟 RPC 或跨节点更新。
    """
    for value, name in [(notice_us, '通知时间'), (change_us, '需求变更'),
                        (end_us, '结束')]:
        _integer(value, name)
    for value, name in [(install_delay_us, '安装延迟'),
                        (report_delay_us, '报告延迟')]:
        _integer(value, name, 0)
    age_certificate(deadline_us, initial_generated_us, [], change_us, end_us)
    if initial_generated_us is None:
        raise ValueError('该下界要求已知初始生成时间')
    for t in packet_times:
        _integer(t, '到包时间')
        if t < change_us or t >= end_us:
            raise ValueError('本接口只接受变更后的窗口内机会')
    if len(set(packet_times)) != len(packet_times):
        raise ValueError('机会时间不能重复')
    effective = notice_us + install_delay_us
    selected = sorted(t for t in packet_times if t >= effective)
    samples = [(t, t + report_delay_us) for t in selected]
    available = samples[0][1] if samples else None
    expiry = initial_generated_us + deadline_us
    waiting_end = min(end_us, available) if available is not None else end_us
    bound = max(0, waiting_end - max(change_us, expiry))
    return {'effective_us': effective, 'first_new_report_us': available,
            'unavoidable_pre_report_violation_us': bound,
            'selected_records': len(selected), 'report_attempts': len(selected),
            'age': age_certificate(deadline_us, initial_generated_us,
                                   samples, change_us, end_us)}
