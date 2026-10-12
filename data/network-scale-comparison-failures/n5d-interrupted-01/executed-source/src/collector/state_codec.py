"""解析版本 1 的队列快照记录，生成时间与门控时间分别处理。"""
from collector.codec import decode_frame as decode_core

STATE_TYPE = 0x88B6
HEADER_BYTES = 8
RECORD_BYTES = 20


def decode_frame(frame: bytes) -> dict | None:
    if len(frame) < 14 or int.from_bytes(frame[12:14], 'big') != STATE_TYPE:
        return decode_core(frame)
    base = decode_core(frame[:12] + b'\x08\x00' + frame[14:])
    if base is None or base['ip_length'] < 20 + HEADER_BYTES:
        raise ValueError('状态基础头截断')
    count, ttl, version, metric, mask, seen = frame[34:40]
    if version != 1 or metric != 1:
        raise ValueError('未知状态版本或指标')
    end = 34 + HEADER_BYTES + count * RECORD_BYTES
    if count > 8 or end > 14 + base['ip_length']:
        raise ValueError('状态项数越界或记录截断')
    records = []
    for offset in range(42, end, RECORD_BYTES):
        records.append({'switch_id': frame[offset], 'hop': frame[offset + 1],
                        'generated_us': int.from_bytes(frame[offset + 2:offset + 8], 'big'),
                        'queue_packets': int.from_bytes(frame[offset + 8:offset + 12], 'big'),
                        'selection_key': int.from_bytes(frame[offset + 12:offset + 20], 'big')})
    base.update(ether_type=STATE_TYPE, count=count, initial_ttl=ttl, version=version,
                metric=metric, allow_mask=mask, seen=seen,
                epoch=int.from_bytes(frame[40:42], 'big'), records=records,
                transport_hex=frame[end:14 + base['ip_length']].hex())
    return base
