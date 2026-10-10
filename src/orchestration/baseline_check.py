"""从受控输入和真实本地时间独立推导门控、权限与字节。"""
from collector.state_codec import HEADER_BYTES, RECORD_BYTES

MODULUS = 1 << 48


def predict(packets, traces, hops):
    states = [dict() for _ in range(hops)]
    expected, observations = {}, []
    inband = [0] * hops
    for packet in packets:
        pid, flow = packet['packet_id'], packet['flow']
        config = packet['configuration']
        mask = config['masks'][str(flow)]
        epoch = config['epochs'][str(flow)]
        scope = config['scope']
        gate_index = 258 if scope == 'port' else flow
        envelope = packet['ip_length'] + HEADER_BYTES <= 1500
        length = packet['ip_length'] + HEADER_BYTES * envelope
        records = []
        for index in range(hops):
            trace = traces[index]
            now = trace['trace_arrival'][pid]
            threshold = config['thresholds'][index][str(flow)] or 100000
            capacity = config['capacities'][index][str(flow)]
            count_before = len(records)
            previous = states[index].get(gate_index)
            if not envelope:
                decision = 4 if index == 0 else 5
            elif not mask & (1 << index):
                decision = 6
            elif previous is not None and (now - previous) % MODULUS < threshold:
                decision = 2
            elif len(records) >= min(capacity, 8):
                decision = 3
            elif length + RECORD_BYTES > 1500:
                decision = 4
            else:
                decision = 1
                length += RECORD_BYTES
                records.insert(0, {'switch_id': index + 1, 'hop': index + 1,
                                    'generated_us': trace['trace_generated'][pid],
                                    'queue_packets': trace['trace_value'][pid], 'selection_key': 0})
                states[index][gate_index] = now
            inband[index] += length - packet['ip_length']
            observations.append({'packet_id': pid, 'node': index + 1, 'flow': flow,
                                 'gate_index': gate_index, 'gate_local_us': now,
                                 'generated_local_us': trace['trace_generated'][pid],
                                 'count_before': count_before, 'mask': mask if envelope else 0,
                                 'epoch': epoch if envelope else 0, 'decision': decision})
        expected[pid] = {'records': records, 'mask': mask, 'epoch': epoch, 'envelope': envelope}
    export = sum(42 + RECORD_BYTES * len(row['records']) for row in expected.values() if row['records'])
    return {'packets': expected, 'observations': observations, 'states': states,
            'cost': {'inband_per_link_bytes': inband, 'export_bytes': export,
                     'total_byte_hops': sum(inband) + export,
                     'control_scope': '数据包测量段不含安装，在线控制另列逻辑命令与本机 RPC 抓包'}}
