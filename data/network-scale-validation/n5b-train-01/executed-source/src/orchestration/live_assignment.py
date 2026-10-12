"""固定三对象的实收报告适配，时钟区间依赖本机发送仪表。"""
from orchestration.assignment_reference import Dependency, Flow, Item, expired, solve_dinto, solve_intopp
from orchestration.deployment import KEYS
from orchestration.slot_reference import WEIGHTS, tt_policy


class ReceivedState:
    """只接纳已经收到并有发送前因果界的报告，不跨节点直接减时间戳。

    本机发送日志是正确性验证仪表，不是已经完成的远程时钟协议。
    固定偏移区间为空或时间戳回绕时拒绝继续，不能静默使用错误年龄。
    """
    def __init__(self):
        self.offsets = {}
        self.latest = {}
        self.seen = set()

    def ingest(self, reports, sends, cutoff_ns):
        for report in sorted(reports, key=lambda r: (r['receive_monotonic_ns'], r['packet_id'])):
            pid, arrival = report['packet_id'], report['receive_monotonic_ns']
            if pid in self.seen or arrival > cutoff_ns or pid not in sends:
                continue
            lower = sends[pid]['send_before_monotonic_ns']
            if lower > arrival or report.get('socket_drop_counter', 0):
                raise ValueError('报告因果界或接收丢包计数异常')
            for item in report['records']:
                node, generated = item['switch_id'], item['generated_us']
                if node not in (1, 2, 3) or not 0 <= generated < 1 << 48:
                    raise ValueError('不支持的节点或时间戳')
                lo, hi = lower - generated * 1000, arrival - generated * 1000
                if node in self.offsets:
                    old_lo, old_hi = self.offsets[node]
                    lo, hi = max(lo, old_lo), min(hi, old_hi)
                if lo > hi:
                    raise ValueError('固定时钟偏移不兼容，可能回绕或时钟变化')
                self.offsets[node] = lo, hi
                self.latest[node] = max(generated, self.latest.get(node, generated))
            self.seen.add(pid)

    def snapshot(self, now_ns):
        values = []
        for node in (1, 2, 3):
            generated = self.latest.get(node)
            if generated is None:
                values.append({'node': node, 'latest_generated_us': None, 'age_lower_us': None,
                               'age_upper_us': None, 'offset_interval_ns': None})
                continue
            lo, hi = self.offsets[node]
            values.append({'node': node, 'latest_generated_us': generated,
                           'age_lower_us': max(0, (now_ns - generated * 1000 - hi) / 1000),
                           'age_upper_us': max(0, (now_ns - generated * 1000 - lo) / 1000),
                           'offset_interval_ns': [lo, hi]})
        return values


class LiveAssignment:
    """调用准确枚举，沿用训练参数和既有逐流门控，没有硬预算监管。"""
    def __init__(self, family, parameters, capacity):
        if family not in ('tt', 'dinto', 'intopp', 'intopp_ahead') or capacity not in (1, 2):
            raise ValueError('不支持的方法或容量')
        self.family, self.parameters, self.capacity = family, dict(parameters), capacity
        self.previous, self.queue = {}, 0.

    def decide(self, deadlines, observations):
        if len(deadlines) != 3 or any(d <= 0 for d in deadlines):
            raise ValueError('需要三个正年龄期限')
        if [r['node'] for r in observations] != [1, 2, 3]:
            raise ValueError('需要按节点排序的三个观察')
        age = {r['node']: r['age_upper_us'] for r in observations}
        result, queue_before = None, self.queue
        if self.family == 'tt':
            target = tt_policy(self.parameters, deadlines).thresholds
        else:
            gains = list(WEIGHTS)
            if self.family == 'dinto' and self.parameters['gain'] == 'age':
                gains = [w * (1 + (3 if age[n] is None else min(3., age[n] / deadlines[n - 1])))
                         for n, w in enumerate(WEIGHTS, 1)]
            items = [Item(n, 1, 12, gains[n - 1]) for n in (1, 2, 3)]
            flows = [Flow(1, (1, 3), self.capacity * 12), Flow(2, (2, 3), self.capacity * 12),
                     Flow(3, (3,), self.capacity * 12)]
            if self.family == 'dinto':
                result = solve_dinto(items, flows, self.previous, self.queue,
                                    self.parameters['tradeoff'], self.parameters['average_change_bytes'],
                                    method='enumeration', flow_order=self.parameters.get('flow_order'))
                self.queue = result['next_queue']
            else:
                ahead = self.parameters['lookahead_us'] if self.family == 'intopp_ahead' else 0
                dependencies = [Dependency(f'task-{n}', (n,), (1,), False,
                    active=expired(age[n], deadlines[n - 1], ahead), weight=w)
                    for n, w in enumerate(WEIGHTS, 1)]
                result = solve_intopp(items, flows, dependencies, self.previous, temporal='distinct',
                                      method='enumeration', flow_order=self.parameters.get('flow_order'))
            self.previous = result['assignment']
            target = {key: 1 if self.previous.get((key[0], 1)) == key[1] else None for key in KEYS}
        return {'thresholds': [{'node': n, 'flow': f, 'threshold_us': v} for (n, f), v in sorted(target.items())],
                'assignment': [{'node': n, 'kind': k, 'flow': f} for (n, k), f in sorted(self.previous.items())],
                'queue_before': queue_before, 'queue_after': self.queue,
                'variation_bytes': result['variation_bytes'] if result else 0,
                'objective': (result.get('p3_objective', result.get('dependency_score')) if result else None)}
