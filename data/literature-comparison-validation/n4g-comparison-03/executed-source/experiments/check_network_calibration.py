"""独立重建实际文件前缀、信息进入视图的上界及完整分块矩阵。"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random

from check_matched_network import check_case as basic_case, sha, load, rows
from check_rolling_reference import same


def check_prefixes(directory):
    case = load(directory / 'result.json')
    raw = (directory / 'collector.jsonl').read_bytes()
    seen, latest, cache, reports = set(), {}, {}, []
    count = 0
    events = []
    for decision in rows(directory / 'decisions.jsonl'):
        if 'view' not in decision:
            continue
        event = decision['availability']
        events.append(event)
        times = [decision['cycle_started_ns'], event['read_started_ns'], event['read_finished_ns'],
                 event['ingest_started_ns'], event['available_ns'], decision['cutoff_ns'], decision['solver_started_ns']]
        if times != sorted(times):
            raise ValueError('视图在实际读取或状态更新完成之前进入求解')
        same(event['cutoff_ns'], decision['cutoff_ns'], '文件批次与决策共同截止')
        if not 0 <= event['prefix_bytes'] <= event['file_bytes'] <= len(raw):
            raise ValueError('读取前缀长度超出实际文件')
        snapshot = raw[:event['file_bytes']]
        prefix = snapshot[:snapshot.rfind(b'\n') + 1]
        same(len(prefix), event['prefix_bytes'], '完整行前缀边界')
        same(hashlib.sha256(prefix).hexdigest(), event['prefix_sha256'], '实际读取前缀摘要')
        batch = [json.loads(line) for line in prefix.splitlines() if line]
        same(len(batch), event['row_count'], '完整文件行数')
        same(len(reports), event['reports_before'], '批次前合法报告数')
        for r in batch:
            if r.get('receive_monotonic_ns', event['read_finished_ns']) > event['read_finished_ns']:
                raise ValueError('实际读取文件含未来物理接收')
            if r.get('ether_type') != 0x88b6:
                continue
            flow = (r['session'], r['flow_tag'])
            records = tuple((x['switch_id'], x['hop'], x['generated_us'], x['queue_packets']) for x in r['records'])
            identity = flow, records
            if identity in seen:
                continue
            seen.add(identity)
            route = (1, 3) if flow == (20, 1001) else (2, 3) if flow == (20, 2002) else None
            if not r['ip_checksum_ok'] or not records or route is None or any(
                    hop < 1 or hop > len(route) or route[hop - 1] != node for node, hop, _, _ in records):
                continue
            fresh = []
            received = (r['receive_monotonic_ns'] - case['origin_monotonic_ns']) // 1000
            for node, hop, generated, value in records:
                old = latest.get((flow, node))
                if old is not None and not 0 < (generated - old) % 2**48 < 2**47:
                    continue
                latest[flow, node] = generated
                fresh.append({'node': node, 'hop': hop, 'generated_local_us': generated, 'queue_packets': value})
                prior = cache.get(node)
                if prior is None or 0 < (generated - prior['generated_local_us']) % 2**48 < 2**47:
                    cache[node] = {'object_id': {'node': node, 'port': 2, 'queue': 0},
                                   'generated_local_us': generated, 'received_us': received, 'queue_packets': value}
            if fresh:
                reports.append({'received_us': received, 'flow': {'session': flow[0], 'tag': flow[1]}, 'records': fresh})
            count += 1
        same(len(reports), event['reports_after'], '批次后合法报告数')
        same(decision['view']['reports'], reports, '在线报告完整来源于实际取得前缀')
        same(decision['view']['cache'], [cache[n] for n in sorted(cache)], '在线共享缓存完整来源于实际取得前缀')
        count += 9
    same(events, rows(directory / 'availability.jsonl'), '完整文件可用性批次留档')
    senders = {r['version']: r for r in rows(directory / 'notice-send.jsonl')}
    schedule = {r['message']['version']: r for r in load(directory / 'notice-schedule.json')}
    notices = rows(directory / 'notices.jsonl')
    same(set(senders), set(schedule), '完整通知发送')
    same([r['message']['version'] for r in notices], list(schedule), '完整通知接收')
    for notice in notices:
        version = notice['message']['version']
        sent, plan = senders[version], schedule[version]
        if not (case['origin_monotonic_ns'] + plan['offset_us'] * 1000 <= sent['before_ns'] <= sent['after_ns']) \
                or not notice['receive_started_ns'] <= notice['received_ns'] or sent['before_ns'] > notice['received_ns']:
            raise ValueError('通知发送/实际接收时序错误')
        # sendto 完成取时可晚于另一线程 recv 完成，二者不假定串行。
        same(sent['payload_hex'], notice['payload_hex'], '通知发送与实际接收字节')
        same(json.loads(bytes.fromhex(sent['payload_hex'])), plan['message'], '实际通知与冻结需求')
        count += 5
    return count


def check_case(directory, rpc):
    result = basic_case(directory, rpc)
    result['availability_checks'] = check_prefixes(directory)
    return result


def audit(run):
    manifest = load(run / 'manifest.json')
    same(manifest['status'], 'passed', '运行完成')
    same(manifest['seed'], 29, '开发种子')
    same(len(manifest['cases']), manifest['expected_cases'], '完整案例分母')
    expected = []
    for block, seed in enumerate(manifest['order_seeds'], 1):
        batch = [(p.rsplit('-', 1)[0], p.rsplit('-', 1)[1], m) for p in manifest['profiles'] for m in manifest['methods']]
        random.Random(seed).shuffle(batch)
        expected.extend({'name': f'b{block:02d}-{s}-{b}-{m}', 'block': block, 'scenario': s,
                         'budget_level': b, 'method': m, 'order_seed': seed} for s, b, m in batch)
    same(expected, manifest['execution_order'], '预声明分块随机顺序')
    same(load(run / 'execution-order.json'), expected, '执行前顺序文件')
    same([c['name'] for c in manifest['cases']], [r['name'] for r in expected], '实际执行完整顺序')
    same(len(manifest['order_seeds']), manifest['blocks'], '完整块数')
    for index, (c, entry) in enumerate(zip(manifest['cases'], expected)):
        for key in ('block', 'scenario', 'budget_level', 'method', 'order_seed'):
            same(c[key], entry[key], '案例分块身份')
        same(c['position'], index, '实际顺序位置')
    for name, digest in manifest['source_sha256'].items():
        same(sha(run / 'executed-source' / name), digest, '执行源码快照')
    for name, digest in manifest['artifact_sha256'].items():
        same(sha(run / name), digest, '完整产物摘要')
    rpc = rows(run / 'rpc-operations.jsonl')
    results = [check_case(run / c['name'], rpc) for c in manifest['cases']]
    if any(r['status'] != 'passed' for r in results):
        raise ValueError('真实业务、费用或配置检查失败')
    return {'status': 'passed', 'cases': results, 'packets': sum(r['packet_count'] for r in results),
            'reports': sum(r['report_count'] for r in results), 'decisions': sum(r['decision_count'] for r in results),
            'checks': sum(r['checks'] + r['live_control_checks'] for r in results),
            'availability_checks': sum(r['availability_checks'] for r in results)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    result = audit(parser.parse_args().run)
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, ensure_ascii=False))
