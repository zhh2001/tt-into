"""确定性正确性业务、共享需求和真实旧身份探针。"""
from scapy.all import Ether, IP, UDP, Raw
from collector.codec import decode_frame
from collector.tagged_codec import WireRecord, encode
from orchestration.event_schema import FlowId


def packet(spec, ordinal, pid, offset_us, probe=None):
    f = spec.record['flows'][ordinal - 1]
    payload = pid.to_bytes(4, 'big') + bytes([ordinal]) * 88
    original = bytes(Ether(src='02:00:00:00:00:01', dst='02:00:00:00:00:02') /
        IP(src='10.0.0.1', dst='10.0.0.2', ttl=64, id=pid) / UDP(sport=f['sport'], dport=9000) / Raw(payload))
    data, identity = original, dict(f['identity'])
    if probe:
        if probe == 'old_session': identity['session'] -= 1
        elif probe not in ('old_report', 'outside_path'): raise ValueError('未知正确性探针')
        node = f['path'][-1] if probe == 'outside_path' else f['entry_node']
        header = encode(FlowId(**identity), (WireRecord(node, 1, 123, 0),), 64)
        data = bytes(Ether(src='02:00:00:00:00:01', dst='02:00:00:00:00:02', type=0x88b6) /
            IP(src='10.0.0.1', dst='10.0.0.2', ttl=64, id=pid, proto=17) / Raw(header + original[34:]))
    return dict(packet_id=pid, flow=ordinal, path=f['path'], source_interface_index=f['source_interface_index'],
        offset_ns=offset_us * 1000, ip_length=120, frame_hex=data.hex(),
        expected_transport_hex=decode_frame(original)['transport_hex'], payload_hex=payload.hex(),
        registered=probe != 'old_session', probe=probe, wire_identity=identity,
        injected_stale_session=probe == 'old_session')


def inputs(spec, mode, base):
    times = [(offset, i + 1, None) for i in range(len(spec.record['flows']))
             for offset in range(10000 + i * 5000, 10000000, 125000)]
    if mode == 'manual':
        times += [(3200000, 1, 'old_session'), (5200000, 1, 'old_report'), (6200000, 1, 'outside_path')]
    times.sort()
    ps = [packet(spec, flow, base + i, offset, probe) for i, (offset, flow, probe) in enumerate(times)]
    nodes = list(range(1, spec.record['sampling_nodes'] + 1))
    notices = [dict(offset_us=0, message=dict(version=1, demands=[dict(node=n, target_us=200000,
        weight=1.0, active=True) for n in nodes])), dict(offset_us=4000000,
        message=dict(version=2, demands=[dict(node=n, target_us=200000,
            weight=3.0 if n == nodes[-1] else 1.0, active=True) for n in nodes]))]
    tasks = [dict(task_id=f'n{n}-part{part}', object_id=dict(node=n, port=2, queue=0),
        start_us=start, end_us=end, target_us=200000, weight=3.0 if part == 1 and n == nodes[-1] else 1.0)
        for part, start, end in ((0, 2000000, 4000000), (1, 4000000, 10000000)) for n in nodes]
    return ps, notices, tasks
