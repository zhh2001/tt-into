"""候选版本 6 的遥测负载编解码。未部署到 P4，不改变已有格式。"""
from dataclasses import dataclass
import struct

from orchestration.event_schema import FlowId, integer

HEADER_BYTES = 12
RECORD_BYTES = 12
VERSION = 6
METRIC = 1
STACK_SLOTS = 8


@dataclass(frozen=True)
class WireRecord:
    node: int
    hop: int
    generated_local_us: int
    queue_packets: int

    def __post_init__(self):
        integer(self.node, '记录节点', 1, 255)
        integer(self.hop, 'HopNum', 1, 253)
        integer(self.generated_local_us, '本地生成时间', 0, 2**48 - 1)
        integer(self.queue_packets, '队列值', 0, 2**32 - 1)


def encode(flow, records, initial_ttl=255):
    integer(initial_ttl, '源 TTL', 2, 255)
    if len(records) > STACK_SLOTS or any(r.hop >= initial_ttl for r in records):
        raise ValueError('记录超出栈容量或 TTL')
    header = struct.pack('!BBBBII', len(records), initial_ttl, VERSION, METRIC, flow.tag, flow.session)
    return header + b''.join(bytes((r.node, r.hop)) + r.generated_local_us.to_bytes(6, 'big') +
                             r.queue_packets.to_bytes(4, 'big') for r in records)


def decode(payload):
    if len(payload) < HEADER_BYTES:
        raise ValueError('候选格式头部截断')
    count, ttl, version, metric, tag, session = struct.unpack('!BBBBII', payload[:HEADER_BYTES])
    if version != VERSION or metric != METRIC or count > STACK_SLOTS:
        raise ValueError('版本、指标或记录数错误')
    if len(payload) != HEADER_BYTES + count * RECORD_BYTES:
        raise ValueError('负载长度不匹配')
    records = []
    for offset in range(HEADER_BYTES, len(payload), RECORD_BYTES):
        records.append(WireRecord(payload[offset], payload[offset + 1],
                                  int.from_bytes(payload[offset + 2:offset + 8], 'big'),
                                  int.from_bytes(payload[offset + 8:offset + 12], 'big')))
    integer(ttl, '源 TTL', 2, 255)
    if any(r.hop >= ttl for r in records):
        raise ValueError('HopNum 超出 TTL')
    return FlowId(session, tag), tuple(records)
