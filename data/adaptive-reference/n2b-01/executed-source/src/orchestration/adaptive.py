"""合法间隔机会、剩余窗口费用估计和有动作门槛的编排原型。"""
from dataclasses import dataclass
import math
import statistics

from collector.tagged_codec import HEADER_BYTES, RECORD_BYTES
from orchestration.event_schema import Hop, Packet
from orchestration.rolling import Budget, OpportunityProxy, choose, config_code, excess


@dataclass(frozen=True)
class AdaptiveSettings:
    gain_margin: float = 0.0
    reserve_fraction: float = 0.0
    lookback_us: int = 3000000
    cluster_fraction: float = 0.25
    period_relative_mad: float = 0.2
    measurement_start_us: int = 2000000
    measurement_end_us: int = 10000000

    def __post_init__(self):
        if not math.isfinite(self.gain_margin) or self.gain_margin < 0:
            raise ValueError('动作门槛须有限且非负')
        if not 0 <= self.reserve_fraction < 1 or self.lookback_us <= 0 or not 0 < self.cluster_fraction < 1:
            raise ValueError('预算或历史参数错误')
        if not 0 <= self.period_relative_mad < 1 or self.measurement_start_us >= self.measurement_end_us:
            raise ValueError('周期偏差或测量窗口错误')


def latest_rates(view):
    previous, rates = {}, {}
    for row in view.counts:
        if row.available_us > view.now_us:
            raise ValueError('计数尚不可用')
        old = previous.get(row.flow)
        if old is not None:
            elapsed = row.available_us - old.available_us
            delta = (row.count - old.count) % (1 << 32)
            if elapsed < 0:
                raise ValueError('计数响应顺序错误')
            if elapsed > 0:
                rates[row.flow] = delta * 1000000 / elapsed if delta < 2**31 else None
        previous[row.flow] = row
    return rates


def unique_reports(view):
    """同节点串行生成顺序只用于剔除重复/旧记录，不跨节点减时间戳。"""
    seen, latest, rows = set(), {}, []
    for report in view.reports:
        identity = report.flow, report.records
        if identity in seen:
            continue
        seen.add(identity)
        advances = False
        for record in report.records:
            key = report.flow, record.node
            old = latest.get(key)
            delta = None if old is None else (record.generated_local_us - old) % (1 << 48)
            if old is None or 0 < delta < 2**47:
                latest[key] = record.generated_local_us
                advances = True
        if advances:
            rows.append(report)
    return tuple(rows)


def profile(view, flow, route, rate, settings, adaptive):
    delay = (len(route) - 1) * settings.nominal_hop_us + 2 * settings.nominal_egress_us + settings.nominal_report_delay_us
    stamps = [r.received_us - delay for r in unique_reports(view)
              if r.flow == flow and view.now_us - adaptive.lookback_us <= r.received_us <= view.now_us]
    base = {'flow': flow, 'rate_pps': rate, 'observed_report_count': len(stamps),
            'time_basis': '接收时刻减名义传输先验，不是源到包真值'}
    if rate is None or rate <= 0:
        return {**base, 'mode': 'no_paid_supply', 'period_us': None, 'anchor_us': None, 'burst_packets': 0}
    period = 1000000 / min(rate, settings.maximum_rate_pps)
    if not stamps:
        return {**base, 'mode': 'uniform_prior', 'period_us': period,
                'anchor_us': view.now_us + ((flow.tag % 31) + .5) / 31 * period, 'burst_packets': 1}
    groups, small = [[stamps[0]]], []
    cut = settings.grid_us[0] * adaptive.cluster_fraction
    for stamp in stamps[1:]:
        gap = stamp - groups[-1][-1]
        if 0 < gap <= cut:
            groups[-1].append(stamp)
            small.append(gap)
        elif gap > 0:
            groups.append([stamp])
    gaps = [b[0] - a[0] for a, b in zip(groups, groups[1:])]
    if len(groups) >= 3 and small:
        burst_period = statistics.median(gaps)
        mad = statistics.median(abs(g - burst_period) for g in gaps) / burst_period
        inner = statistics.median(small)
        count = max(max(map(len, groups)), round(min(rate, settings.maximum_rate_pps) * burst_period / 1000000))
        if mad <= adaptive.period_relative_mad and count >= 2 and inner * (count - 1) < burst_period / 4:
            return {**base, 'mode': 'periodic_cluster', 'period_us': burst_period, 'anchor_us': groups[-1][0],
                    'burst_packets': count, 'inner_spacing_us': inner, 'period_relative_mad': mad,
                    'visible_cluster_count': len(groups), 'hidden_positions': '按计数和群内间隔外推，属于机会假设'}
    return {**base, 'mode': 'count_period_with_report_phase', 'period_us': period,
            'anchor_us': stamps[-1], 'burst_packets': 1, 'visible_cluster_count': len(groups)}


def forecast_packets(profiles, view, management, settings):
    packets = []
    for p in profiles:
        if not p['burst_packets']:
            continue
        route, period, anchor = management.routes[p['flow']], p['period_us'], p['anchor_us']
        # 从可能跨当前时刻的群开始，所有拟议事件严格晚于当前时刻。
        block = anchor + math.floor((view.now_us - anchor) / period) * period
        end = view.now_us + settings.horizon_us
        emitted = 0
        limit = math.ceil(settings.maximum_rate_pps * settings.horizon_us / 1000000)
        while block < end and emitted < limit:
            for index in range(p['burst_packets']):
                stamp = block + index * p.get('inner_spacing_us', 0)
                source = int(math.ceil(stamp))
                if not view.now_us < source < end or emitted >= limit:
                    continue
                hops = tuple(Hop(o, management.links[o], source + i * settings.nominal_hop_us,
                                 source + i * settings.nominal_hop_us + settings.nominal_egress_us)
                             for i, o in enumerate(route))
                sink = hops[-1].egress_us + settings.nominal_egress_us
                packets.append(Packet(len(packets), p['flow'], hops, sink,
                                      (sink + settings.nominal_report_delay_us,), settings.nominal_ip_bytes))
                emitted += 1
            block += period
    return tuple(packets)


def spend_estimate(view, management, rates, settings, adaptive, history):
    """报告费用为下界，历史预测为补充估计，不声称丢失尝试已准确恢复。"""
    start = adaptive.measurement_start_us
    headers = dict.fromkeys(management.links.values(), 0.0)
    source_counts = []
    for f, route in management.routes.items():
        counts = [r for r in view.counts if r.flow == f]
        anchor = next((r for r in counts if r.issued_us >= start), None)
        last = counts[-1] if counts else None
        count = 0.0
        if anchor is not None and last is not None:
            delta = (last.count - anchor.count) % (1 << 32)
            if delta < 2**31:
                # 响应间计数和未观察尾段的估计，不能称窗口内精确包数。
                count = delta + max(0, rates.get(f) or 0) * max(0, view.now_us - last.available_us) / 1000000
        source_counts.append({'flow': f, 'estimated_window_packets': count,
                              'anchor_read_id': None if anchor is None else anchor.read_id,
                              'last_read_id': None if last is None else last.read_id})
        for o in route:
            headers[management.links[o]] += HEADER_BYTES * count
    received_meta = dict.fromkeys(headers, 0.0)
    received_export = 0
    for report in unique_reports(view):
        if report.received_us < start:
            continue
        received_export += max(60, 34 + HEADER_BYTES + RECORD_BYTES * len(report.records))
        route = management.routes[report.flow]
        for record in report.records:
            for o in route[record.hop - 1:]:
                received_meta[management.links[o]] += RECORD_BYTES
    past = [h for h in history if start <= h['time_us'] < view.now_us]
    predicted_meta = {link: sum(max(0, h['cost']['inband_per_link_bytes'].get(link, 0) - h['header_bytes'].get(link, 0))
                               for h in past) for link in headers}
    predicted_export = sum(h['cost']['export_attempt_bytes'] for h in past)
    control = sum(sum(h['cost']['control_serialized_bytes_by_kind'].values()) for h in past)
    return {'inband_bytes': {link: headers[link] + max(received_meta[link], predicted_meta[link]) for link in headers},
            'export_bytes': max(received_export, predicted_export), 'control_bytes': control,
            'header_bytes': headers, 'received_metadata_lower_bytes': received_meta,
            'predicted_metadata_bytes': predicted_meta, 'received_export_lower_bytes': received_export,
            'predicted_export_bytes': predicted_export, 'source_count_inputs': source_counts,
            'scope': '计数尾段与未收尝试仍是估计，不是实际费用真值或严格上界'}


def remaining_budget(view, management, budget, rates, settings, adaptive, history):
    if any(h['time_us'] >= view.now_us for h in history):
        raise ValueError('预算历史包含未来或当前预测')
    spend = spend_estimate(view, management, rates, settings, adaptive, history)
    duration = (adaptive.measurement_end_us - adaptive.measurement_start_us) / 1000000
    remaining = (adaptive.measurement_end_us - max(view.now_us, adaptive.measurement_start_us)) / 1000000
    if remaining <= 0:
        raise ValueError('剩余预算窗口已经结束')

    def rate(cap, used):
        return max(0.0, (duration * cap - used) / remaining) * (1 - adaptive.reserve_fraction)

    effective = Budget({k: rate(v, spend['inband_bytes'].get(k, 0)) for k, v in budget.link_bytes_per_s.items()},
                       rate(budget.export_bytes_per_s, spend['export_bytes']), rate(budget.control_bytes_per_s, spend['control_bytes']))
    return effective, {'spend': spend, 'remaining_s': remaining,
                       'nominal_budget': budget, 'effective_budget': effective,
                       'reserve_fraction': adaptive.reserve_fraction}


class AdaptiveProxy(OpportunityProxy):
    def __init__(self, view, management, budget, settings, adaptive, history=(), temporal=True, guarded=True):
        self.adaptive, self.temporal, self.guarded = adaptive, temporal, guarded
        self.profiles = ()
        super().__init__(view, management, budget, settings)
        self.ready = all(self.rates.get(f) is not None for f in management.routes)
        self.effective_budget, self.budget_diagnostic = remaining_budget(view, management, budget, self.rates, settings, adaptive, history)

    def _packets(self):
        if not self.temporal:
            return super()._packets()
        self.rates = latest_rates(self.view)
        self.profiles = tuple(profile(self.view, f, route, self.rates.get(f), self.settings, self.adaptive)
                              for f, route in sorted(self.management.routes.items()))
        return forecast_packets(self.profiles, self.view, self.management, self.settings)

    def evaluate(self, config):
        row = super().evaluate(config)
        if 'effective_budget_excess' not in row:
            row['effective_budget_excess'] = excess(row['cost'], self.effective_budget)
            row['guard_enabled'] = self.guarded
        return row

    def rank(self, row, local=False):
        if not self.guarded:
            return super().rank(row, local)
        return (round(row['effective_budget_excess'], 12), row['local_loss' if local else 'predicted_loss'],
                row['update_rpc_count'], config_code(row['config']))


def post_notice_counts_ready(view, management):
    if not view.notifications:
        return True
    notice = max(view.notifications, key=lambda n: (n.version, n.received_us))
    return all(sum(r.flow == f and r.issued_us > notice.received_us for r in view.counts) >= 2 for f in management.routes)


def select(proxy, trained, local=False):
    selected = choose(proxy, 'local' if local else 'joint', trained)
    if selected['forecast'] is None or not proxy.guarded:
        return selected
    current = proxy.evaluate(proxy.current)
    if not post_notice_counts_ready(proxy.view, proxy.management):
        selected.update(config=proxy.current, reason='awaiting_post_notice_counts', forecast=current)
        return selected
    best = selected['forecast']
    repairing = round(best['effective_budget_excess'], 12) < round(current['effective_budget_excess'], 12)
    objective = 'local_loss' if local else 'predicted_loss'
    improvement = current[objective] - best[objective]
    if not repairing and improvement < proxy.adaptive.gain_margin:
        selected.update(config=proxy.current, reason='gain_below_calibrated_margin', forecast=current)
    selected['predicted_objective_improvement'] = improvement
    selected['budget_repair_action'] = repairing and selected['config'] != proxy.current
    return selected


def history_entry(proxy, decision):
    row = decision['forecast']
    if row is None:
        return None
    headers = dict.fromkeys(proxy.management.links.values(), 0)
    for p in proxy.packets:
        for hop in p.hops:
            headers[hop.link] += HEADER_BYTES
    return {'time_us': proxy.view.now_us, 'cost': row['cost'], 'header_bytes': headers,
            'basis': '过去自身选择的在线预测，不是执行器账本'}
