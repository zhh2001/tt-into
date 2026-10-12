"""按实际身份、路径和同节点生成顺序构建合法在线视图。"""
from collector.tagged_frame import STATE_TYPE
from collector.tagged_codec import WireRecord
from orchestration.event_schema import Demand, FlowId, ObjectId
from orchestration.event_observation import CacheState, DemandNotification, OnlineView, ReceivedReport


class IntoState:
    def __init__(self, routes, origin_ns):
        self.routes, self.origin_ns = routes, origin_ns
        self.seen, self.latest, self.cache = set(), {}, {}
        self.reports, self.counts, self.notifications, self.ingestion = [], [], [], []

    def ingest(self, rows, cutoff_ns):
        for row in rows:
            if row.get('ether_type') != STATE_TYPE or row.get('receive_monotonic_ns', cutoff_ns + 1) > cutoff_ns:
                continue
            flow = FlowId(row['session'], row['flow_tag'])
            records = tuple(WireRecord(r['switch_id'], r['hop'], r['generated_us'], r['queue_packets']) for r in row['records'])
            identity = flow, records
            if identity in self.seen:
                continue
            self.seen.add(identity)
            reason = None
            if not row['ip_checksum_ok'] or not records:
                reason = 'invalid_report'
            elif flow not in self.routes:
                reason = 'unknown_identity_or_session'
            elif any(r.hop > len(self.routes[flow]) or self.routes[flow][r.hop - 1].node != r.node for r in records):
                reason = 'outside_registered_route'
            if reason is not None:
                self.ingestion.append({'flow': flow, 'received_ns': row['receive_monotonic_ns'], 'accepted': False, 'reason': reason})
                continue
            received = (row['receive_monotonic_ns'] - self.origin_ns) // 1000
            fresh = []
            for record in records:
                key = flow, record.node
                old = self.latest.get(key)
                delta = None if old is None else (record.generated_local_us - old) % 2**48
                if old is not None and not 0 < delta < 2**47:
                    continue
                self.latest[key] = record.generated_local_us
                fresh.append(record)
                obj = self.routes[flow][record.hop - 1]
                cached = self.cache.get(obj)
                advance = None if cached is None else (record.generated_local_us - cached.generated_local_us) % 2**48
                if cached is None or 0 < advance < 2**47:
                    self.cache[obj] = CacheState(obj, record.generated_local_us, received, record.queue_packets)
            if fresh:
                self.reports.append(ReceivedReport(received, flow, tuple(fresh)))
            self.ingestion.append({'flow': flow, 'received_ns': row['receive_monotonic_ns'],
                                   'accepted': bool(fresh), 'reason': 'fresh' if fresh else 'old_record'})

    def notify(self, message, received_ns):
        if self.notifications and message['version'] <= self.notifications[-1].version:
            raise ValueError('需求通知版本未递增')
        demands = tuple(Demand(ObjectId(r['node']), r['target_us'], r['weight'], r['active']) for r in message['demands'])
        if len({d.object_id for d in demands}) != len(demands):
            raise ValueError('通知重复对象')
        self.notifications.append(DemandNotification((received_ns - self.origin_ns) // 1000, message['version'], demands))

    def snapshot(self, cutoff_ns, confirmations):
        now = (cutoff_ns - self.origin_ns) // 1000
        return OnlineView(now, tuple(r for r in self.reports if r.received_us <= now),
                          tuple(r for r in self.counts if r.available_us <= now),
                          tuple(r for r in confirmations if r.received_us <= now),
                          tuple(self.cache[k] for k in sorted(self.cache) if self.cache[k].received_us <= now),
                          tuple(r for r in self.notifications if r.received_us <= now))
