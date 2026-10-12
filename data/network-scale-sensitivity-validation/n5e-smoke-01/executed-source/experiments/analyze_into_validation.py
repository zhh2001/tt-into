"""由完整抓包与独立核对生成公开正确性证据，不提供方法排名。"""
import argparse
from collections import Counter
from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from scapy.utils import RawPcapReader
from check_into_validation import audit, load, rows, wire

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def analyze(run, target):
    results = audit(run)
    manifest = load(run / 'manifest.json')
    target.mkdir()
    manifest['raw_manifest_sha256'] = hashlib.sha256((run / 'manifest.json').read_bytes()).hexdigest()
    manifest['raw_artifact_sha256'] = manifest.pop('artifact_sha256')
    manifest['cases'] = [dict(c, independent_check=r) for c, r in zip(manifest['cases'], results)]
    packets = {p['packet_id']: (c['name'], p) for c in manifest['cases'] for p in load(run / c['name'] / 'packets.json')}
    costs = {c['name']: {'inband_per_link_bytes': dict.fromkeys(('e1', 'e2', 'e3'), 0),
                       'export_attempt_bytes': 0, 'export_attempt_padded_bytes': 0, 'frames': Counter(), 'wire': []}
             for c in manifest['cases']}
    seen = set()
    for path in sorted(run.glob('node-*/*_out.pcap')):
        node = int(path.parent.name.split('-')[1])
        port = 2 if path.name.endswith('-eth2_out.pcap') else 3 if node == 4 and path.name.endswith('-eth3_out.pcap') else None
        if port is None:
            continue
        archived = target / path.parent.name / path.name
        archived.parent.mkdir(exist_ok=True)
        shutil.copyfile(path, archived)
        with closing(RawPcapReader(str(path))) as reader:
            for frame, _ in reader:
                if len(frame) < 34 or frame[26:34] != b'\x0a\x00\x00\x01\x0a\x00\x00\x02':
                    continue
                r = wire(frame)
                if not r:
                    continue
                pid = r['packet_id']
                if pid not in packets or (pid, node, port) in seen:
                    raise ValueError('未知或重复业务输出')
                seen.add((pid, node, port))
                name, p = packets[pid]
                cost = costs[name]
                extra = r['ip_length'] - p['ip_length']
                if r['ip_length'] > 1500:
                    raise ValueError('链路 MTU 超限')
                if node < 4:
                    if node not in p['path'] or extra != (12 + 12 * len(r['records']) if r['flow'] else 0):
                        raise ValueError('真实路径或附加字节不符')
                    if r['flow'] and r['flow'] != ((19, 1001) if p['injected_stale_session'] else (20, 1001 if p['flow'] == 1 else 2002)):
                        raise ValueError('链路身份不一致')
                    if len(r['records']) > 1 or any(x['switch_id'] not in p['path'][:p['path'].index(node) + 1] for x in r['records']):
                        raise ValueError('容量或采样资格错误')
                    cost['inband_per_link_bytes'][f'e{node}'] += extra
                elif port == 2:
                    if extra or r['flow'] or r['transport_hex'] != p['expected_transport_hex']:
                        raise ValueError('sink 未恢复业务')
                else:
                    if not r['flow'] or len(r['records']) != 1 or r['ip_length'] != 44:
                        raise ValueError('导出尝试格式错误')
                    cost['export_attempt_bytes'] += len(frame)
                    cost['export_attempt_padded_bytes'] += max(60, len(frame))
                cost['frames'][f'{node}:{port}'] += 1
                cost['wire'].append({'packet_id': pid, 'node': node, 'port': port, 'frame_bytes': len(frame),
                                    'ip_bytes': r['ip_length'], 'inband_extra_bytes': extra if node < 4 else 0,
                                    'count': len(r['records']), 'session': r['flow'][0] if r['flow'] else '',
                                    'flow_tag': r['flow'][1] if r['flow'] else '',
                                    'record_nodes': ','.join(str(x['switch_id']) for x in r['records'])})
    rpc = rows(run / 'rpc-operations.jsonl')
    for case in manifest['cases']:
        name, directory = case['name'], run / case['name']
        copies = target / name
        copies.mkdir()
        for path in directory.iterdir():
            if path.suffix in ('.json', '.jsonl', '.pcap'):
                shutil.copyfile(path, copies / path.name)
        case_packets = load(directory / 'packets.json')
        cost = costs[name]
        expected = Counter({f'{n}:2': sum(n in p['path'] for p in case_packets) for n in (1, 2, 3)})
        expected['4:2'], expected['4:3'] = len(case_packets), case['report_count']
        if cost['frames'] != expected:
            raise ValueError('真实输出数量与业务、报告不一致')
        if any(v > manifest['declared_correctness_limits']['case_inband_per_link_bytes'] for v in cost['inband_per_link_bytes'].values()) or \
                cost['export_attempt_padded_bytes'] > manifest['declared_correctness_limits']['case_export_padded_bytes']:
            raise ValueError('正确性数据限额超限')
        with (copies / 'wire.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(cost['wire'][0]), lineterminator='\n')
            writer.writeheader()
            writer.writerows(cost.pop('wire'))
        origins = case['origin_monotonic_ns']
        sends = {r['packet_id']: r for r in rows(directory / 'send.jsonl')}
        intervals, offsets = [], {n: [] for n in (1, 2, 3)}
        for report in rows(directory / 'collector.jsonl'):
            if report.get('ether_type') != 0x88b6 or report['session'] != 20:
                continue
            lower = sends[report['packet_id']]['send_before_monotonic_ns']
            upper = report['receive_monotonic_ns']
            if lower > upper:
                raise ValueError('发送与接收因果关系错误')
            for record in report['records']:
                n = record['switch_id']
                offsets[n].append((lower - record['generated_us'] * 1000, upper - record['generated_us'] * 1000))
                intervals.append({'packet_id': report['packet_id'], 'node': n, 'generation_lower_us': (lower - origins) / 1000,
                                  'generation_upper_us': (upper - origins) / 1000,
                                  'received_us': (upper - origins) / 1000, 'local_generated_us': record['generated_us']})
        clock = {str(n): {'compatible': max(v[0] for v in pairs) <= min(v[1] for v in pairs),
                          'intersection_width_us': max(0, min(v[1] for v in pairs) - max(v[0] for v in pairs)) / 1000,
                          'incompatible_gap_us': max(0, max(v[0] for v in pairs) - min(v[1] for v in pairs)) / 1000}
                 if pairs else {'compatible': None, 'intersection_width_us': None, 'incompatible_gap_us': None}
                 for n, pairs in offsets.items()}
        write(copies / 'generation-intervals.json', {'intervals': intervals, 'local_offset_compatibility': clock,
              'scope': '同宿主发送前至实际接收的因果区间，时间戳仅作条件兼容检查，非精确生成 AoI；旧会话注入项排除'})
        start, end = [origins + v * 1000 for v in case['measurement_window_us']]
        in_window = [r for r in rpc if r['kind'] in ('online_observation', 'update', 'reconciliation', 'controlled_failed_read')]
        api_bytes = sum(r['request_serialized_bytes'] for r in in_window if start <= r['before_monotonic_ns'] < end) + \
                    sum(r['reply_serialized_bytes'] for r in in_window if start <= r['after_monotonic_ns'] < end)
        local_bytes = sum(r['payload_bytes'] for r in rows(directory / 'notices.jsonl') if start <= r['received_ns'] < end)
        cost.update(frames=dict(cost['frames']), online_api_serialized_bytes_in_window=api_bytes,
                    online_api_mean_bytes_per_s=api_bytes / ((end - start) / 1e9),
                    local_notice_payload_bytes_in_window=local_bytes,
                    scope='全部业务及预热的数据费用，控制另按声明窗口；API/本地消息/捕获帧分列，非性能预算排名')
        case['measured_cost'] = cost
        case['generation_offset_compatible'] = all(v['compatible'] is not False for v in clock.values())
    control = rows(run / 'rpc.jsonl')
    if any(r['socket_drop_counter'] for r in control):
        raise ValueError('控制捕获套接字丢包')
    frame_bytes = sum(r['frame_bytes'] for r in control)
    if frame_bytes > manifest['declared_correctness_limits']['whole_run_control_frame_bytes']:
        raise ValueError('控制正确性限额超限')
    groups = {}
    for r in rpc:
        group = groups.setdefault(r['kind'], {'calls': 0, 'failed_calls': 0, 'request_serialized_bytes': 0, 'reply_serialized_bytes': 0})
        group['calls'] += 1
        group['failed_calls'] += not r['success']
        for k in ('request_serialized_bytes', 'reply_serialized_bytes'):
            group[k] += r[k]
    manifest.update(control_cost={'metered_api_by_kind': groups, 'loopback_captured_frame_bytes': frame_bytes,
                                  'loopback_frame_count': len(control),
                                  'scope': '全程含初始化、CLI、TCP、验证和 GSO；捕获帧不按时间归因单 RPC，不与 API 相加'},
                    wire_checks={'output_frames': len(seen), 'passed': True},
                    fair_performance_comparison_completed=False, hard_budget_guarantee=False)
    for name in ('rpc-operations.jsonl', 'rpc.jsonl', 'unit-tests.txt', 'frozen-inputs.json'):
        shutil.copyfile(run / name, target / name)
    shutil.copytree(run / 'executed-source', target / 'executed-source')
    manifest['artifact_sha256'] = {str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in target.rglob('*') if p.is_file()}
    write(target / 'manifest.json', manifest)
    return {'variant': target.name, 'cases': len(results), 'packets': sum(r['packet_count'] for r in results),
            'reports': sum(r['report_count'] for r in results), 'checks': sum(r['checks'] for r in results),
            'adaptive_provenance_checks': sum(r['adaptive_provenance_checks'] for r in results),
            'output_frames': len(seen), 'control_capture_bytes': frame_bytes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--ordinary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    summaries = [analyze(args.core, args.output / 'diagnostics'), analyze(args.ordinary, args.output / 'ordinary')]
    regression = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=ROOT,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (args.output / 'unit-tests.txt').write_text(regression.stdout)
    if regression.returncode:
        raise ValueError('最终回归失败')
    source_names = ('experiments/analyze_into_validation.py', 'experiments/check_into_validation.py',
                    'experiments/check_adaptive_reference.py', 'experiments/check_rolling_reference.py')
    for name in source_names:
        p = args.output / 'executed-analysis-source' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, p)
    record = {'status': 'passed', 'scope': '五类案例、两版本的软件正确性，非统计性能',
              'variants': summaries, 'unit_regression': {'exit_code': regression.returncode},
              'input_manifest_sha256': {k: hashlib.sha256((p / 'manifest.json').read_bytes()).hexdigest() for k, p in
                                        (('diagnostics', args.core), ('ordinary', args.ordinary))},
              'analysis_source_sha256': {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in source_names},
              'artifact_sha256': {str(p.relative_to(args.output)): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in args.output.rglob('*') if p.is_file()}}
    write(args.output / 'manifest.json', record)
    print(json.dumps({'status': 'passed', 'variants': summaries}, ensure_ascii=False))


if __name__ == '__main__':
    main()
