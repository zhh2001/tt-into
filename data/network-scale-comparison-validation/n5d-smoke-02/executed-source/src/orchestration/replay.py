"""沿真实事件顺序执行参考策略，不把报告送达等同于本地插入成功。"""
import math
import random
from orchestration.model import HEADER_BYTES, RECORD_BYTES, EXPORT_FIXED_BYTES

POLICIES = ['tt_prefix', 'shared_prefix', 'uniform_reservoir', 'weighted_reservoir',
            'rotation', 'static_flow']


def replay(tasks, packets, threshold_us, policy, seed=0, static_assignment=None, initial_gate=None):
    thresholds = threshold_us if isinstance(threshold_us, dict) else {task.node: threshold_us for task in tasks}
    if policy not in POLICIES or any(thresholds[task.node] < 0 for task in tasks):
        raise ValueError('未知策略或负阈值')
    rng = random.Random(seed)
    weights = {task.node: task.weight for task in tasks}
    last = dict(initial_gate or {})
    selected = {p.packet_id: {} for p in packets}
    allowed, counters = {}, []
    hops = len(packets[0].path)
    inband = [0] * hops
    for index, packet in enumerate(sorted(packets, key=lambda p: (p.gate_us[0], p.packet_id))):
        if policy == 'rotation':
            allowed[packet.packet_id] = {packet.path[(index + offset) % hops] for offset in range(packet.slots())}
        elif policy == 'static_flow':
            allowed[packet.packet_id] = set((static_assignment or {}).get(packet.flow, packet.path[:packet.slots()]))
        else:
            allowed[packet.packet_id] = set(packet.path)
    events = sorted((p.generated_us[i], i, p.packet_id, p) for p in packets for i in range(hops))
    for _, index, pid, packet in events:
        node = packet.path[index]
        key = (node, 0 if policy == 'shared_prefix' else packet.flow)
        gate = packet.gate_us[index]
        current = selected[pid]
        inserted, removed = False, None
        eligible = packet.envelope() and node in allowed[pid] and (key not in last or gate - last[key] >= thresholds[node])
        if eligible and packet.slots():
            if 'reservoir' in policy:
                weight = weights[node] if policy == 'weighted_reservoir' else 1.0
                priority = -math.log(max(rng.random(), 1e-300)) / weight
                if len(current) < packet.slots():
                    current[node] = priority
                    inserted = True
                else:
                    worst = max(current, key=current.get)
                    if priority < current[worst]:
                        removed = worst
                        del current[worst]
                        current[node] = priority
                        inserted = True
            elif len(current) < packet.slots():
                current[node] = 0.0
                inserted = True
        if inserted:
            last[key] = gate
        # 替换不能撤销上游成功门控，其记录在前段链路仍然占用字节。
        inband[index] += HEADER_BYTES * packet.envelope() + RECORD_BYTES * len(current)
        counters.append({'packet_id': pid, 'node': node, 'eligible': eligible,
                         'inserted': inserted, 'evicted_node': removed, 'gate_us': gate})
    final = {pid: sorted(records) for pid, records in selected.items()}
    export = sum(EXPORT_FIXED_BYTES + RECORD_BYTES * len(records) for records in final.values() if records)
    return {'selected': final, 'events': counters,
            'cost': {'inband_per_link_bytes': inband, 'export_bytes': export,
                     'control_bytes_in_window': 0, 'total_byte_hops': sum(inband) + export},
            'gate_scope': 'port' if policy == 'shared_prefix' else 'flow',
            'replacement_resets_upstream_gate': False}
