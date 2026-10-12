"""从实收前缀独立重建任意已声明合流路径的观察状态。"""
import copy
import hashlib
import json
from check_rolling_reference import same


def replay(raw, observations, decisions, origin, period_us, duration_us, decision_us=1000000, routes=None):
    if routes is None:
        raise ValueError('必须显式提供合法路径')
    seen, latest, cache, reports, snapshots = set(), {}, {}, [], []
    checks, advances, previous_bytes = 0, 0, 0
    same(len(observations), duration_us // period_us, '完整报告观察槽位')
    for index, observed in enumerate(observations):
        scheduled = origin + index * period_us * 1000
        same(observed['observation_index'], index, '连续观察编号')
        same(observed['scheduled_offset_us'], index * period_us, '观察预定相对时间')
        same(observed['scheduled_ns'], scheduled, '观察预定绝对时间')
        start, finish = observed['cycle_started_ns'], observed['finished_ns']
        if not scheduled <= start <= finish:
            raise ValueError('观察槽位早于预定时间或完成倒序')
        late = start >= scheduled + period_us * 1000
        same(observed['skipped'], late, '整观察槽迟到才跳过')
        same(observed['lateness_ns'], start - scheduled, '实际观察迟到')
        same(observed['decision_due'], index * period_us % decision_us == 0, '独立秒级决策节拍')
        same(observed['decision_skipped'], start >= scheduled + decision_us * 1000, '独立决策迟到标记')
        checks += 9
        if late:
            if 'availability' in observed or 'fresh_reports' in observed:
                raise ValueError('跳过槽位不能产生观察数据')
            continue
        event = observed['availability']
        same(event['observation_index'], index, '批次观察身份')
        same(event['scheduled_offset_us'], index * period_us, '批次预定时间')
        times = [start, event['read_started_ns'], event['read_finished_ns'], event['ingest_started_ns'],
                 event['available_ns'], finish]
        if times != sorted(times):
            raise ValueError('读取与状态更新实际完成顺序错误')
        same(observed['rpc_start_index'], observed['rpc_stop_index'], '报告tick不发起RPC')
        if not previous_bytes <= event['file_bytes'] <= len(raw) or not 0 <= event['prefix_bytes'] <= event['file_bytes']:
            raise ValueError('观察前缀倒退或越过归档文件')
        previous_bytes = event['file_bytes']
        snapshot = raw[:event['file_bytes']]
        prefix = snapshot[:snapshot.rfind(b'\n') + 1]
        same(len(prefix), event['prefix_bytes'], '只使用完整行')
        same(hashlib.sha256(prefix).hexdigest(), event['prefix_sha256'], '实际读取摘要')
        batch = [json.loads(line) for line in prefix.splitlines() if line]
        same(len(batch), event['row_count'], '完整行数')
        before = len(reports)
        same(before, event['reports_before'], '此前合法报告数量')
        for r in batch:
            if r.get('receive_monotonic_ns', event['read_finished_ns'] + 1) > event['read_finished_ns']:
                raise ValueError('前缀含读取完成之后的接收记录')
            if r.get('ether_type') != 0x88b6:
                continue
            flow = r['session'], r['flow_tag']
            records = tuple((x['switch_id'], x['hop'], x['generated_us'], x['queue_packets']) for x in r['records'])
            identity = flow, records
            if identity in seen:
                continue
            seen.add(identity)
            route = routes.get(flow)
            if not r['ip_checksum_ok'] or not records or route is None or any(
                    hop < 1 or hop > len(route) or route[hop - 1] != node for node, hop, _, _ in records):
                continue
            fresh = []
            received = (r['receive_monotonic_ns'] - origin) // 1000
            for node, hop, generated, value in records:
                prior = latest.get((flow, node))
                if prior is not None and not 0 < (generated - prior) % 2**48 < 2**47:
                    continue
                latest[flow, node] = generated
                fresh.append({'node': node, 'hop': hop, 'generated_local_us': generated, 'queue_packets': value})
                old = cache.get(node)
                if old is None or 0 < (generated - old['generated_local_us']) % 2**48 < 2**47:
                    cache[node] = {'object_id': {'node': node, 'port': 2, 'queue': 0},
                                   'generated_local_us': generated, 'received_us': received, 'queue_packets': value}
                    advances += 1
            if fresh:
                reports.append({'received_us': received, 'flow': {'session': flow[0], 'tag': flow[1]}, 'records': fresh})
            checks += 1
        same(len(reports), event['reports_after'], '本批完成后报告数量')
        same(observed['fresh_reports'], reports[before:], '实际首次取得的新报告')
        same(observed['cache_after'], [cache[n] for n in sorted(cache)], '实际更新后的共享缓存')
        snapshots.append({'index': index, 'available_ns': event['available_ns'], 'reports': copy.deepcopy(reports),
                          'cache': copy.deepcopy(observed['cache_after'])})
        checks += 11
    same(len(decisions), duration_us // decision_us, '完整秒级决策分母')
    for index, decision in enumerate(decisions):
        same(decision['scheduled_offset_us'], index * decision_us, '决策预定时间')
        cutoff = decision.get('cutoff_ns', decision['cycle_finished_ns'])
        preceding = [s for s in snapshots if s['available_ns'] <= cutoff]
        selected = preceding[-1] if preceding else {'index': None, 'reports': [], 'cache': []}
        same(decision['observation_index'], selected['index'], '只引用截止前最新完成批次')
        if 'view' in decision:
            same(decision['view']['reports'], selected['reports'], '决策报告来自实际批次')
            same(decision['view']['cache'], selected['cache'], '决策缓存来自实际批次')
        elif not decision.get('skipped'):
            raise ValueError('执行决策缺少合法视图')
        checks += 4
    return {'observer_checks': checks, 'observation_slots': len(observations), 'completed_observations': len(snapshots),
            'skipped_observations': len(observations) - len(snapshots), 'visible_reports': len(reports),
            'shared_cache_advances': advances}
