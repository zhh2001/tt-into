"""固定路径下的合法机会代理、预算评分和有限邻域策略。"""
from dataclasses import dataclass
from itertools import product
import math

from orchestration.event_engine import EventEngine
from orchestration.event_metrics import _clipped_area, window_costs
from orchestration.event_observation import estimate_rates
from orchestration.event_schema import Hop, Node, Packet, RpcCost, Task, Write


@dataclass(frozen=True)
class Settings:
    grid_us: tuple[int, ...] = (50000, 200000, 1000000)
    epoch_us: int = 1000000
    horizon_us: int = 1000000
    alpha: float = 0.5
    clip: float = 4.0
    max_candidates: int = 48
    max_update_rpcs: int = 6
    capacity: int = 1
    nominal_ip_bytes: int = 120
    nominal_hop_us: int = 20
    nominal_egress_us: int = 5
    nominal_report_delay_us: int = 1000
    maximum_rate_pps: float = 100.0

    def __post_init__(self):
        if not self.grid_us or tuple(sorted(set(self.grid_us))) != self.grid_us or min(self.grid_us) <= 0:
            raise ValueError('阈值网格须互异递增且为正')
        if self.epoch_us <= 0 or self.horizon_us <= 0 or self.max_candidates < 1 or self.max_update_rpcs < 0:
            raise ValueError('周期、时域或求解限额错误')
        if not 0 < self.alpha <= 1 or self.clip <= 0 or not 1 <= self.capacity <= 8 or self.maximum_rate_pps <= 0:
            raise ValueError('预测参数错误')


@dataclass(frozen=True)
class Budget:
    link_bytes_per_s: dict
    export_bytes_per_s: float
    control_bytes_per_s: float = 120.0

    def __post_init__(self):
        if any(not math.isfinite(v) or v < 0 for v in
               list(self.link_bytes_per_s.values()) + [self.export_bytes_per_s, self.control_bytes_per_s]):
            raise ValueError('预算必须有限且非负')


@dataclass(frozen=True)
class Management:
    routes: dict
    links: dict
    rpc_cost: RpcCost
    write_costs: dict | None = None
    read_cost: RpcCost | None = None
    notice_cost: RpcCost | None = None

    @property
    def keys(self):
        return tuple((obj.node, flow) for flow in sorted(self.routes) for obj in self.routes[flow])

    def cost_for_write(self, field):
        return self.rpc_cost if self.write_costs is None else self.write_costs[field]


def configurations(management, settings):
    return product((None,) + settings.grid_us, repeat=len(management.keys))


def config_code(config):
    return tuple(-1 if value is None else value for value in config)


def fields_from_confirmations(view, management):
    fields, versions = {}, {}
    for key in management.keys:
        fields[key] = {'enabled': False, 'threshold_us': 100000}
    for row in view.confirmations:
        key = row.node, row.flow
        if not row.success or key not in fields:
            continue
        stamp = row.version, row.received_us
        if stamp >= versions.get((key, row.field), (-1, -1)):
            fields[key][row.field] = row.value
            versions[key, row.field] = stamp
    return fields


def configuration_from_fields(fields, management):
    return tuple(fields[key]['threshold_us'] if fields[key]['enabled'] else None for key in management.keys)


def changes(config, fields, management):
    """开通前先写阈值，禁用时保留阈值与成功门控。"""
    rows = []
    for key, value in zip(management.keys, config):
        old = fields[key]
        if value is None:
            if old['enabled']:
                rows.append((key, 'enabled', False))
        else:
            if old['threshold_us'] != value:
                rows.append((key, 'threshold_us', value))
            if not old['enabled']:
                rows.append((key, 'enabled', True))
    return rows


def current_demands(view):
    latest = {}
    for notice in view.notifications:
        for demand in notice.demands:
            previous = latest.get(demand.object_id)
            if previous is None or notice.version >= previous[0]:
                latest[demand.object_id] = notice.version, demand
    return tuple(latest[key][1] for key in sorted(latest) if latest[key][1].active)


def loss(tasks, receipts, clip):
    denominator = sum(t.weight * (t.end_us - t.start_us) for t in tasks)
    if denominator == 0:
        return 0.0
    return sum(t.weight * _clipped_area(
        [(r['generated_us'], r['received_us']) for r in receipts if r['object_id'] == t.object_id],
        t.start_us, t.end_us, t.target_us, clip) for t in tasks) / denominator


def installation(config, management, settings, time_us=-1000):
    rows = []
    for i, (key, value) in enumerate(zip(management.keys, config)):
        node, flow = key
        for field, data in [('threshold_us', settings.grid_us[0] if value is None else value),
                            ('enabled', value is not None)]:
            rows.append(Write(f'install-{i}-{field}', node, flow, field, data, 1,
                              time_us, time_us + 1, time_us + 2, management.cost_for_write(field), 'installation'))
    return tuple(rows)


def excess(cost, budget):
    pairs = [(cost['inband_per_link_bytes_per_s'].get(link, 0), cap) for link, cap in budget.link_bytes_per_s.items()]
    pairs += [(cost['export_attempt_bytes_per_s'], budget.export_bytes_per_s),
              (sum(cost['control_serialized_bytes_by_kind'].values()) / ((cost['window_us'][1] - cost['window_us'][0]) / 1000000),
               budget.control_bytes_per_s)]
    return sum(max(0.0, value / cap - 1) if cap else value for value, cap in pairs)


class OpportunityProxy:
    """仅由 OnlineView 和管理常量构造的均匀机会预测，不接收实际引擎。"""

    def __init__(self, view, management, budget, settings):
        histories = ((view.reports, 'received_us'), (view.counts, 'available_us'),
                     (view.confirmations, 'received_us'), (view.cache, 'received_us'),
                     (view.notifications, 'received_us'))
        if any(getattr(row, clock) > view.now_us for rows, clock in histories for row in rows):
            raise ValueError('在线视图包含尚不可用的观察')
        self.view, self.management, self.budget, self.settings = view, management, budget, settings
        self.fields = fields_from_confirmations(view, management)
        self.current = configuration_from_fields(self.fields, management)
        self.rates = {r.flow: r.rate_pps for r in estimate_rates(view, settings.alpha)}
        self.ready = all(self.rates.get(flow) is not None for flow in management.routes)
        self.demands = current_demands(view)
        self.tasks = tuple(Task(d.object_id.label, d.object_id, view.now_us, view.now_us + settings.horizon_us,
                                d.target_us, d.weight) for d in self.demands)
        self.seeds = [{'object_id': c.object_id, 'generated_us': c.received_us,
                       'received_us': c.received_us, 'accepted': True} for c in view.cache]
        self.last_estimate, latest_local = {}, {}
        for report in view.reports:
            route = management.routes[report.flow]
            for record in report.records:
                key = record.node, report.flow
                old = latest_local.get(key)
                delta = None if old is None else (record.generated_local_us - old) % (1 << 48)
                if old is None or 0 < delta < 2**47:
                    latest_local[key] = record.generated_local_us
                    remaining = len(route) - record.hop
                    # 名义传输先验，不把本地生成时间当成全局年龄。
                    self.last_estimate[key] = report.received_us - settings.nominal_report_delay_us - \
                        remaining * settings.nominal_hop_us - settings.nominal_egress_us * 2
        self.packets = self._packets()
        self.memo = {}

    def _packets(self):
        packets = []
        for flow in sorted(self.management.routes):
            rate = self.rates.get(flow)
            if rate is None or rate <= 0:
                continue
            rate = min(rate, self.settings.maximum_rate_pps)
            spacing = 1000000 / rate
            phase = ((flow.tag % 31) + 0.5) / 31
            time = self.view.now_us + phase * spacing
            route = self.management.routes[flow]
            while time < self.view.now_us + self.settings.horizon_us:
                source = int(math.ceil(time))
                hops = tuple(Hop(obj, self.management.links[obj], source + i * self.settings.nominal_hop_us,
                                 source + i * self.settings.nominal_hop_us + self.settings.nominal_egress_us)
                             for i, obj in enumerate(route))
                sink = hops[-1].egress_us + self.settings.nominal_egress_us
                packets.append(Packet(len(packets), flow, hops, sink,
                                      (sink + self.settings.nominal_report_delay_us,), self.settings.nominal_ip_bytes))
                time += spacing
        return tuple(packets)

    def evaluate(self, config):
        if config in self.memo:
            return self.memo[config]
        node_ids = sorted({obj.node for route in self.management.routes.values() for obj in route})
        engine = EventEngine([Node(n) for n in node_ids], self.management.routes, self.packets,
                             installation(config, self.management, self.settings, self.view.now_us - 1000),
                             capacity=self.settings.capacity)
        engine.run_until(self.view.now_us)
        for key, stamp in self.last_estimate.items():
            engine.gates[key].last_local_us = stamp % (1 << 48)
        result = engine.run()
        end = self.view.now_us + self.settings.horizon_us
        cost = window_costs(result['cost_events'], self.view.now_us, end)
        diffs = changes(config, self.fields, self.management)
        rpc_count = len(diffs)
        read_cost = self.management.read_cost or self.management.rpc_cost
        notify_cost = self.management.notice_cost or self.management.rpc_cost
        notice_cost = sum(n.received_us == self.view.now_us for n in self.view.notifications) * \
            (notify_cost.request_bytes + notify_cost.reply_bytes)
        update_bytes = sum(self.management.cost_for_write(field).request_bytes + self.management.cost_for_write(field).reply_bytes
                           for _, field, _ in diffs)
        observe_bytes = len(self.management.routes) * (read_cost.request_bytes + read_cost.reply_bytes) + notice_cost
        cost['control_serialized_bytes_by_kind'] = {'installation': 0, 'observation': observe_bytes, 'update': update_bytes}
        independent = []
        gates = dict(self.last_estimate)
        policy = dict(zip(self.management.keys, config))
        for p in sorted(self.packets, key=lambda p: (p.hops[0].ingress_us, p.packet_id)):
            for hop in p.hops:
                key = hop.object_id.node, p.flow
                threshold = policy[key]
                if threshold is not None and (key not in gates or (hop.ingress_us - gates[key]) % (1 << 48) >= threshold):
                    gates[key] = hop.ingress_us
                    independent.append({'object_id': hop.object_id, 'generated_us': hop.egress_us,
                                        'received_us': p.report_arrivals_us[0], 'accepted': True})
        row = {'config': config, 'predicted_loss': loss(self.tasks, self.seeds + result['receipts'], self.settings.clip),
               'local_loss': loss(self.tasks, self.seeds + independent, self.settings.clip),
               'cost': cost, 'predicted_excess': excess(cost, self.budget), 'update_rpc_count': rpc_count,
               'update_allowed': rpc_count <= self.settings.max_update_rpcs}
        self.memo[config] = row
        return row

    def rank(self, row, local=False):
        return (round(row['predicted_excess'], 12), row['local_loss' if local else 'predicted_loss'],
                row['update_rpc_count'], config_code(row['config']))


def shifted(config, direction, settings):
    grid = settings.grid_us
    return tuple(None if value is None else grid[min(len(grid) - 1, max(0, grid.index(value) + direction))]
                 for value in config)


def neighbors(config, settings):
    options = (None,) + settings.grid_us
    for i in range(len(config)):
        for value in options:
            if value != config[i]:
                row = list(config)
                row[i] = value
                yield tuple(row)
    for i in range(len(config)):
        for j in range(i + 1, len(config)):
            if config[i] != config[j]:
                row = list(config)
                row[i], row[j] = row[j], row[i]
                yield tuple(row)


def observed_utilization(proxy):
    """已收报告与付费源计数的费用代理，丢报时不能视为导出真值。"""
    duration = proxy.settings.epoch_us / 1000000
    links = dict.fromkeys(proxy.budget.link_bytes_per_s, 0.0)
    for flow, route in proxy.management.routes.items():
        rate = proxy.rates.get(flow) or 0
        for obj in route:
            link = proxy.management.links[obj]
            links[link] = links.get(link, 0.0) + 12 * rate
    exported, seen = 0, set()
    for report in proxy.view.reports:
        if not proxy.view.now_us - proxy.settings.epoch_us <= report.received_us < proxy.view.now_us:
            continue
        identity = report.flow, report.records
        if identity in seen:
            continue
        seen.add(identity)
        exported += max(60, 46 + 12 * len(report.records))
        route = proxy.management.routes[report.flow]
        for record in report.records:
            for obj in route[record.hop - 1:]:
                link = proxy.management.links[obj]
                links[link] = links.get(link, 0.0) + 12 / duration
    ratios = [value / proxy.budget.link_bytes_per_s[link] if proxy.budget.link_bytes_per_s[link] else math.inf if value else 0
              for link, value in links.items()]
    ratios.append(exported / duration / proxy.budget.export_bytes_per_s if proxy.budget.export_bytes_per_s else
                  math.inf if exported else 0)
    return max(ratios, default=0.0)


def choose(proxy, method, trained):
    if method not in ('static', 'feedback', 'local', 'joint'):
        raise ValueError('未知方法')
    if method == 'static' or not proxy.ready or not proxy.demands:
        return {'config': proxy.current, 'reason': 'static' if method == 'static' else 'insufficient_observations',
                'evaluated_candidates': 0, 'forecast': None}
    if method == 'feedback':
        utilization = observed_utilization(proxy)
        step = 1 if utilization > 1 else -1 if utilization < 0.85 else 0
        selected = proxy.evaluate(shifted(proxy.current, step, proxy.settings))
        if not selected['update_allowed']:
            selected = proxy.evaluate(proxy.current)
        return {'config': selected['config'], 'reason': 'common_threshold_feedback',
                'observed_utilization_proxy': utilization, 'evaluated_candidates': len(proxy.memo), 'forecast': selected}
    local = method == 'local'
    selected = proxy.evaluate(proxy.current)
    pool = [trained, shifted(proxy.current, -1, proxy.settings), shifted(proxy.current, 1, proxy.settings)]
    while len(proxy.memo) < proxy.settings.max_candidates:
        before = selected['config']
        candidates = pool + list(neighbors(selected['config'], proxy.settings))
        pool = []
        for config in dict.fromkeys(candidates):
            if config in proxy.memo:
                row = proxy.memo[config]
            elif len(proxy.memo) < proxy.settings.max_candidates:
                row = proxy.evaluate(config)
            else:
                break
            if row['update_allowed'] and proxy.rank(row, local) < proxy.rank(selected, local):
                selected = row
        if selected['config'] == before:
            break
    return {'config': selected['config'], 'reason': 'local_proxy_with_capacity_cost' if local else 'joint_capacity_proxy',
            'evaluated_candidates': len(proxy.memo), 'forecast': selected}


def exact_proxy(proxy):
    rows = [proxy.evaluate(c) for c in configurations(proxy.management, proxy.settings)]
    return min((r for r in rows if r['update_allowed']), key=proxy.rank)
