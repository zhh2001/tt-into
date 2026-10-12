"""训练选中与旧共同初始化的完整双业务采样位置和条件界诊断。"""
import argparse
from pathlib import Path
import shutil

from analyze_observer_training import write
from check_matched_network import load, rows, sha
from check_rolling_reference import same

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('experiments/diagnose_observer_training.py', 'tests/test_observer_training_diagnostics.py')


def business_packet(packet):
    payload = bytes.fromhex(packet['payload_hex'])
    frame = bytearray.fromhex(packet['frame_hex'])
    transport = bytearray.fromhex(packet['expected_transport_hex'])
    if (len(payload) < 4 or int.from_bytes(payload[:4], 'big') != packet['packet_id']
            or frame[12:14] != b'\x08\x00' or frame[14] != 0x45
            or frame[23] != 17 or frame[34:] != transport or transport[8:] != payload
            or int.from_bytes(frame[18:20], 'big') != packet['packet_id']):
        raise ValueError('包身份或固定IPv4/UDP仪表格式不一致')
    # 只消去验证PID及其引起的校验和差异，保留业务内容、长度和全部其余字段。
    frame[18:20] = bytes(2)
    frame[24:26] = bytes(2)
    frame[40:42] = bytes(2)
    frame[42:46] = bytes(4)
    transport[6:8] = bytes(2)
    transport[8:12] = bytes(4)
    value = {k: v for k, v in packet.items() if k not in
             ('packet_id', 'frame_hex', 'expected_transport_hex', 'payload_hex')}
    value.update(frame_hex=frame.hex(), expected_transport_hex=transport.hex(),
                 payload_hex=(bytes(4) + payload[4:]).hex())
    return value


def sample_positions(directory):
    packets = load(directory / 'packets.json')
    indices = {p['packet_id']: i for i, p in enumerate(packets)}
    if len(indices) != len(packets): raise ValueError('包验证身份重复')
    positions = sorted((indices[r['packet_id']], record['switch_id']) for r in rows(directory / 'collector.jsonl')
                       if r.get('ether_type') == 0x88b6 for record in r['records'])
    if len(set(positions)) != len(positions): raise ValueError('普通K=1训练存在重复采样位置')
    business = [business_packet(p) for p in packets]
    return business, positions


def difference(a, b):
    return {'lower': a['loss_lower'] - b['loss_upper'], 'upper': a['loss_upper'] - b['loss_lower'],
            'direction': 'lower' if a['loss_upper'] < b['loss_lower'] else 'higher' if a['loss_lower'] > b['loss_upper'] else 'overlap'}


def diagnose(analysis, checks, output):
    m, audit = load(analysis / 'manifest.json'), load(checks)
    if audit['status'] != 'passed' or audit['cases'] != 192 or audit['analysis_manifest_sha256'] != sha(analysis / 'manifest.json'):
        raise ValueError('诊断需要绑定完整训练独立验收')
    candidates = load(analysis / 'candidate-plan.json')
    selections = load(analysis / 'training-selections.json'); records, bound = [], {}
    for selection in selections:
        if selection['selected_candidate'] is None: continue
        group = next(g for g in candidates['groups'] if g['scenario'] == selection['scenario'])
        previous = next(c['candidate_index'] for c in group['candidates'] if c['config'] == selection['previous_common_initialization'])
        for seed in (11, 29):
            def case(index):
                return next(c for c in m['cases'] if c['scenario'] == selection['scenario'] and c['seed'] == seed and c['candidate_index'] == index)
            a, b = case(selection['selected_candidate']), case(previous)
            da, db = analysis / a['name'], analysis / b['name']
            ba, pa = sample_positions(da); bb, pb = sample_positions(db)
            same(ba, bb, '选中与旧参照使用完整相同业务')
            same(load(da / 'tasks.json'), load(db / 'tasks.json'), '完整任务分母')
            ca, cb = load(da / 'window-costs.json'), load(db / 'window-costs.json')
            record = {'scenario': selection['scenario'], 'budget_level': selection['budget_level'], 'seed': seed,
                'selected_case': a['name'], 'previous_case': b['name'], 'selected_candidate': a['candidate_index'],
                'previous_candidate': b['candidate_index'], 'same_sample_positions': pa == pb,
                'selected_records': len(pa), 'previous_records': len(pb),
                'selected_window_status': ca['window_feasibility'], 'previous_window_status': cb['window_feasibility']}
            for observer, name in (('collector', 'generation-bounds.json'), ('controller', 'controller-visible-bounds.json')):
                record[observer + '_difference'] = difference(load(da / name), load(db / name))
            record['cost_upper_difference_bytes_per_s'] = {}
            for resource in ('e1', 'e2', 'e3', 'export'):
                va, vb = ca['measurement_upper_bytes'], cb['measurement_upper_bytes']
                record['cost_upper_difference_bytes_per_s'][resource] = (va[resource] - vb[resource]) / 8 if va is not None and vb is not None else None
            record['cost_upper_difference_bytes_per_s'].update(api_serialized=ca['api_bytes_per_s'] - cb['api_bytes_per_s'],
                local_notice_payload=ca['local_notice_bytes_per_s'] - cb['local_notice_bytes_per_s'])
            records.append(record)
            for directory in (da, db):
                for name in ('packets.json', 'tasks.json', 'collector.jsonl', 'window-costs.json', 'generation-bounds.json', 'controller-visible-bounds.json'):
                    p = directory / name; relative = str(p.relative_to(analysis))
                    digest = sha(p)
                    same(digest, m['artifact_sha256'][relative], '完整诊断源产物摘要')
                    bound[relative] = digest
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'selected-transfer.json', records)
    findings = {'pairs': len(records), 'business_seed_count': 2,
        'same_sample_position_pairs': sum(r['same_sample_positions'] for r in records),
        'controller_directions': {d: sum(r['controller_difference']['direction'] == d for r in records) for d in ('lower', 'higher', 'overlap')},
        'scope': '已消费训练选中配置与旧初始化的事后完整诊断；不是独立性能测试、显著性或新增机制收益'}
    write(output / 'findings.json', findings)
    for source in SOURCES:
        target = output / 'executed-source' / source
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT / source, target)
    write(output / 'manifest.json', {'status': 'passed', 'analysis_manifest_sha256': sha(analysis / 'manifest.json'),
        'independent_checks_sha256': sha(checks), 'input_artifact_sha256': bound, 'findings': findings,
        'source_sha256': {source: sha(ROOT / source) for source in SOURCES},
        'artifact_sha256': {str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}})
    print(__import__('json').dumps(findings, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('analysis', 'checks', 'output'): p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args(); diagnose(a.analysis, a.checks, a.output)
