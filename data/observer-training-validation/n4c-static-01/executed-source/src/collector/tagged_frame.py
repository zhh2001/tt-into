"""版本 6 的链路帧解析，合法身份来自线格式而非 IPv4 ID。"""
from collector.codec import decode_frame as plain
from collector.tagged_codec import HEADER_BYTES, RECORD_BYTES, decode

STATE_TYPE = 0x88B6


def decode_frame(frame):
    if len(frame) < 14 or int.from_bytes(frame[12:14], 'big') != STATE_TYPE:
        return plain(frame)
    row = plain(frame[:12] + b'\x08\x00' + frame[14:])
    if row is None or row['ip_length'] < 20 + HEADER_BYTES:
        raise ValueError('身份头截断')
    count = frame[34]
    end = 34 + HEADER_BYTES + count * RECORD_BYTES
    if end > 14 + row['ip_length']:
        raise ValueError('身份记录截断')
    flow, records = decode(frame[34:end])
    row.update(ether_type=STATE_TYPE, version=6, count=count, initial_ttl=frame[35],
               flow_tag=flow.tag, session=flow.session,
               records=[{'switch_id': r.node, 'hop': r.hop, 'generated_us': r.generated_local_us,
                         'queue_packets': r.queue_packets} for r in records],
               transport_hex=frame[end:14 + row['ip_length']].hex())
    return row
