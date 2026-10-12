#!/usr/bin/env python3
"""命名空间中的原始包收发及 UDP/TCP 应用检查。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import select
import signal
import socket
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from collector.codec import decode_frame


def pcap_write(handle, frame: bytes, timestamp_ns: int) -> None:
    seconds, remainder = divmod(timestamp_ns, 1_000_000_000)
    handle.write(struct.pack("<IIII", seconds, remainder // 1000,
                             len(frame), len(frame)))
    handle.write(frame)


def capture(args) -> None:
    raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    raw.bind((args.iface, 0))
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    raw.setsockopt(socket.SOL_SOCKET, 35, 1)  # Linux SO_TIMESTAMPNS
    raw.setblocking(False)
    sockets = [raw]
    udp = tcp = None
    if args.serve:
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(("0.0.0.0", 9000)); udp.setblocking(False)
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp.bind(("0.0.0.0", 9001)); tcp.listen(); tcp.setblocking(False)
        sockets += [udp, tcp]
    alive = True

    def stop(*_):
        nonlocal alive
        alive = False

    signal.signal(signal.SIGTERM, stop)
    peers = []
    with open(args.pcap, "wb") as pcap, open(args.events, "w") as events:
        pcap.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        pcap.flush()
        Path(args.ready).write_text("ready\n")
        while alive and not Path(args.stop).exists():
            ready, _, _ = select.select(sockets + peers, [], [], 0.02)
            for channel in ready:
                if channel is raw:
                    frame, ancillary, _, address = raw.recvmsg(65535, 128)
                    if address[2] == socket.PACKET_OUTGOING:
                        continue
                    timestamp_ns = time.time_ns()
                    for level, kind, value in ancillary:
                        if level == socket.SOL_SOCKET and kind == 35:
                            sec, ns = struct.unpack("@ll", value[:16])
                            timestamp_ns = sec * 1_000_000_000 + ns
                    pcap_write(pcap, frame, timestamp_ns)
                    try:
                        record = decode_frame(frame)
                    except ValueError as exc:
                        record = {"decode_error": str(exc)}
                    if record:
                        record.update(kind="frame", receive_time_ns=timestamp_ns)
                        events.write(json.dumps(record) + "\n")
                elif channel is udp:
                    payload, address = udp.recvfrom(65535)
                    events.write(json.dumps({"kind": "udp_application",
                                             "payload_hex": payload.hex()}) + "\n")
                elif channel is tcp:
                    peer, _ = tcp.accept(); peer.setblocking(False); peers.append(peer)
                else:
                    payload = channel.recv(65535)
                    if payload:
                        events.write(json.dumps({"kind": "tcp_application",
                                                 "payload_hex": payload.hex()}) + "\n")
                    else:
                        peers.remove(channel); channel.close()
            pcap.flush(); events.flush()
    for channel in sockets + peers:
        channel.close()


def send(args) -> None:
    packets = json.loads(Path(args.packets).read_text())
    raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    raw.bind((args.iface, 0))
    start = time.monotonic_ns()
    with open(args.events, "w") as events:
        for packet in packets:
            deadline = start + packet["offset_ns"]
            while True:
                remaining = deadline - time.monotonic_ns()
                if remaining <= 0:
                    break
                time.sleep(min(remaining / 1_000_000_000, 0.01))
            before = time.time_ns()
            length = raw.send(bytes.fromhex(packet["frame_hex"]))
            after = time.time_ns()
            events.write(json.dumps({"packet_id": packet["packet_id"],
                                     "send_before_ns": before, "send_after_ns": after,
                                     "sent_length": length,
                                     "schedule_lateness_ns": max(0, time.monotonic_ns() - deadline)}) + "\n")
    raw.close()


def tcp_send(args) -> None:
    payload = bytes.fromhex(args.payload)
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connection.settimeout(5)
    connection.bind(("10.0.0.1", 20101))
    connection.connect(("10.0.0.2", 9001))
    connection.sendall(payload)
    connection.shutdown(socket.SHUT_WR)
    connection.close()
    print(json.dumps({"sent_bytes": len(payload)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    cap = sub.add_parser("capture")
    for name in ["iface", "pcap", "events", "ready", "stop"]:
        cap.add_argument("--" + name, required=True)
    cap.add_argument("--serve", action="store_true")
    tx = sub.add_parser("send")
    for name in ["iface", "packets", "events"]:
        tx.add_argument("--" + name, required=True)
    tcp = sub.add_parser("tcp-send")
    tcp.add_argument("--payload", required=True)
    args = parser.parse_args()
    {"capture": capture, "send": send, "tcp-send": tcp_send}[args.mode](args)
