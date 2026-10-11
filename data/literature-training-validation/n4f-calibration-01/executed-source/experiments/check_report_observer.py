"""独立重建百毫秒前缀、共享缓存及秒级决策引用，不导入观察调度器。"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from check_matched_network import check_case as basic_case, load, rows, sha
from check_rolling_reference import same
from analyze_network_static import check_cache_and_budget
from report_observer_contract import validate


def replay(raw, observations, decisions, origin, period_us, duration_us, decision_us=1000000):
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
            route = {(20, 1001): (1, 3), (20, 2002): (2, 3)}.get(flow)
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


def check_case(directory, rpc):
    result = basic_case(directory, rpc)
    case = load(directory / 'result.json')
    observations, decisions = rows(directory / 'report-observations.jsonl'), rows(directory / 'decisions.jsonl')
    result.update(replay((directory / 'collector.jsonl').read_bytes(), observations, decisions,
                         case['origin_monotonic_ns'], case['observation_period_us'], case['duration_us'], case['decision_period_us']))
    same([r['availability'] for r in observations if not r['skipped']], rows(directory / 'availability.jsonl'), '全部观察事件留档')
    used_reads = [r['rpc_index'] for d in decisions for r in d.get('reads', [])]
    actual_reads = [i for i in range(case['rpc_start_index'], case['rpc_stop_index']) if rpc[i]['kind'] == 'online_observation']
    same(used_reads, actual_reads, '付费源计数只属于秒级决策')
    same(len(used_reads), 0 if case['method'] == 'static' else 2 * sum('view' in d and not d.get('skipped') for d in decisions),
         '观察频率不改变每秒付费计数数量')
    used_writes = [r['rpc_index'] for d in decisions for r in d.get('writes', [])]
    same(used_writes, [i for i in range(case['rpc_start_index'], case['rpc_stop_index']) if rpc[i]['kind'] == 'update'],
         '配置更新仅属于秒级决策')
    result['cache_and_budget_checks'] = check_cache_and_budget(directory)
    senders = {r['version']: r for r in rows(directory / 'notice-send.jsonl')}
    planned = {r['message']['version']: r for r in load(directory / 'notice-schedule.json')}
    notices = rows(directory / 'notices.jsonl')
    same(set(senders), set(planned), '全部通知真实发送')
    same([r['message']['version'] for r in notices], list(planned), '完整通知接收')
    for n in notices:
        s, p = senders[n['message']['version']], planned[n['message']['version']]
        if not case['origin_monotonic_ns'] + p['offset_us'] * 1000 <= s['before_ns'] <= s['after_ns'] \
                or not s['before_ns'] <= n['received_ns'] or not n['receive_started_ns'] <= n['received_ns']:
            raise ValueError('通知真实发送与接收顺序错误')
        same(s['payload_hex'], n['payload_hex'], '通知实收编码')
        same(json.loads(bytes.fromhex(s['payload_hex'])), p['message'], '实收通知与冻结需求')
        if not any(d['cycle_started_ns'] <= n['received_ns'] <= d['cutoff_ns'] for d in decisions if 'view' in d):
            raise ValueError('通知读取发生在秒级决策之外')
        result['observer_checks'] += 4
    return result


def audit(run):
    manifest, plan = load(run / 'manifest.json'), load(run / 'observer-plan.json')
    validate(plan)
    same(manifest['status'], 'passed', '完整实际执行')
    same(sha(run / 'observer-plan.json'), manifest['plan_sha256'], '执行前计划摘要')
    same(manifest['execution_order'], plan['execution_order'], '冻结执行顺序')
    same(load(run / 'execution-order.json'), plan['execution_order'], '独立顺序留档')
    same([c['name'] for c in manifest['cases']], [c['name'] for c in plan['execution_order']], '八案例分母与顺序')
    same(manifest['source_sha256'], plan['source_sha256'], '执行前与实际源码一致')
    for name, digest in manifest['source_sha256'].items():
        same(sha(run / 'executed-source' / name), digest, '实际执行源码快照')
    for name, digest in manifest['artifact_sha256'].items():
        same(sha(run / name), digest, '全部原始产物摘要')
    rpc = rows(run / 'rpc-operations.jsonl')
    results = []
    for c, entry in zip(manifest['cases'], plan['execution_order']):
        for key in entry:
            same(c[key], entry[key], '冻结方法及业务')
        results.append(check_case(run / c['name'], rpc))
    keys = ('packet_count', 'report_count', 'decision_count', 'successful_updates', 'checks', 'live_control_checks',
            'observer_checks', 'cache_and_budget_checks', 'observation_slots', 'completed_observations',
            'skipped_observations', 'visible_reports', 'shared_cache_advances')
    return dict(status='passed', cases=results, **{k: sum(r[k] for r in results) for k in keys})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--run', type=Path, required=True)
    result = audit(p.parse_args().run)
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, ensure_ascii=False))
