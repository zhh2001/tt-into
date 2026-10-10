"""解析独立版本的固定路径适配格式，不改变既有采集器。"""
from collector.codec import decode_frame as decode_plain

FORMATS = {
    'common': {'id': 0, 'version': 2, 'header': 8, 'record': 20, 'reservoir': True},
    'compact': {'id': 1, 'version': 3, 'header': 8, 'record': 12, 'reservoir': True},
    'task': {'id': 2, 'version': 4, 'header': 5, 'record': 12, 'reservoir': True},
    'prefix': {'id': 3, 'version': 5, 'header': 4, 'record': 12, 'reservoir': False},
}
STATE_TYPE = 0x88B6


def decode_frame(frame):
    if len(frame) < 14 or int.from_bytes(frame[12:14], 'big') != STATE_TYPE:
        return decode_plain(frame)
    plain = decode_plain(frame[:12] + b'\x08\x00' + frame[14:])
    if not plain or plain['ip_length'] < 24:
        raise ValueError('基础头截断')
    count, ttl, version, metric = frame[34:38]
    formats = [f for f in FORMATS.values() if f['version'] == version]
    if not formats or metric != 1 or count > 8:
        raise ValueError('未知版本、指标或项数')
    fmt = formats[0]
    end = 34 + fmt['header'] + count * fmt['record']
    if end > 14 + plain['ip_length']:
        raise ValueError('状态项截断')
    records = []
    for offset in range(34 + fmt['header'], end, fmt['record']):
        records.append({'switch_id': frame[offset], 'hop': frame[offset + 1],
                        'generated_us': int.from_bytes(frame[offset + 2:offset + 8], 'big'),
                        'queue_packets': int.from_bytes(frame[offset + 8:offset + 12], 'big')})
    plain.update(ether_type=STATE_TYPE, version=version, count=count, initial_ttl=ttl,
                 allow_mask=frame[38] if fmt['header'] == 8 else 255,
                 epoch=int.from_bytes(frame[40:42], 'big') if fmt['header'] == 8 else 0,
                 seen=frame[39] if fmt['header'] == 8 else frame[38] if fmt['header'] == 5 else 0,
                 records=records, transport_hex=frame[end:14 + plain['ip_length']].hex())
    return plain
