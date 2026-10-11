"""解码 TT-INT 基础头与记录，不依赖报文中存在传输层头。"""
from __future__ import annotations

import socket
import struct

IPV4_TYPE = 0x0800
TELEMETRY_TYPE = 0x88B5
MAX_RECORDS = 8


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def decode_frame(frame: bytes) -> dict | None:
    if len(frame) < 34:
        return None
    ether_type = int.from_bytes(frame[12:14], "big")
    if ether_type not in (IPV4_TYPE, TELEMETRY_TYPE):
        return None
    ip = frame[14:34]
    if ip[0] != 0x45:
        raise ValueError("仅支持无选项的 IPv4")
    length = int.from_bytes(ip[2:4], "big")
    if length < 20 or len(frame) < 14 + length:
        raise ValueError("IPv4 长度不足")
    result = {
        "ether_type": ether_type,
        "packet_id": int.from_bytes(ip[4:6], "big"),
        "src": socket.inet_ntoa(ip[12:16]),
        "dst": socket.inet_ntoa(ip[16:20]),
        "protocol": ip[9], "ttl": ip[8], "ip_length": length,
        "ip_checksum_ok": checksum(ip) == 0,
        "frame_length": len(frame),
    }
    offset = 34
    if ether_type == TELEMETRY_TYPE:
        if length < 22:
            raise ValueError("遥测基础头不足")
        count, initial_ttl = frame[34:36]
        if count > MAX_RECORDS or length < 22 + 2 * count:
            raise ValueError("遥测项数越界或记录截断")
        offset = 36 + 2 * count
        records = [dict(zip(("switch_id", "hop"), frame[i:i + 2]))
                   for i in range(36, offset, 2)]
        result.update(count=count, initial_ttl=initial_ttl, records=records)
    result["transport_hex"] = frame[offset:14 + length].hex()
    return result
