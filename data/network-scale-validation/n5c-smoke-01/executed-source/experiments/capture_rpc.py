"""在本机 loopback 单方向计数 Thrift 帧，原始载荷仅留本地。"""
import argparse
import json
from pathlib import Path
import select
import socket
import struct
import time
from packet_io import pcap_write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['events', 'pcap', 'ready', 'stop']:
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--ports', nargs='+', type=int, required=True)
    args = parser.parse_args()
    raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    raw.bind(('lo', 0))
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
    raw.setsockopt(socket.SOL_SOCKET, 35, 1)
    raw.setsockopt(socket.SOL_SOCKET, 40, 1)  # Linux SO_RXQ_OVFL
    ports = set(args.ports)
    with open(args.events, 'w') as events, open(args.pcap, 'wb') as pcap:
        pcap.write(struct.pack('<IHHIIII', 0xA1B2C3D4, 2, 4, 0, 0, 262144, 1))
        Path(args.ready).write_text('ready\n')
        while not Path(args.stop).exists():
            if not select.select([raw], [], [], 0.02)[0]:
                continue
            frame, ancillary, _, address = raw.recvmsg(262144, 256)
            if address[2] != socket.PACKET_OUTGOING or len(frame) < 54 or frame[12:14] != b'\x08\x00' or frame[23] != 6:
                continue
            offset = 14 + (frame[14] & 15) * 4
            source = int.from_bytes(frame[offset:offset + 2], 'big')
            destination = int.from_bytes(frame[offset + 2:offset + 4], 'big')
            if not {source, destination} & ports:
                continue
            timestamp = time.time_ns()
            dropped = 0
            for level, kind, data in ancillary:
                if level == socket.SOL_SOCKET and kind == 35:
                    sec, ns = struct.unpack('@ll', data[:16])
                    timestamp = sec * 1000000000 + ns
                if level == socket.SOL_SOCKET and kind == 40:
                    dropped = struct.unpack('I', data[:4])[0]
            pcap_write(pcap, frame, timestamp)
            events.write(json.dumps({'time_ns': timestamp, 'frame_bytes': len(frame),
                                     'ip_bytes': int.from_bytes(frame[16:18], 'big'),
                                     'source_port': source, 'destination_port': destination,
                                     'socket_drop_counter': dropped}) + '\n')
            events.flush()
    raw.close()


if __name__ == '__main__':
    main()
