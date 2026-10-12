"""扩展格式的抓包与发送，记录宿主时钟相对单调时钟的变化。"""
import argparse
import json
from pathlib import Path
import socket
import time
import packet_io
from collector.state_codec import decode_frame

packet_io.decode_frame = decode_frame


def clock_pair():
    left = time.monotonic_ns()
    wall = time.time_ns()
    right = time.monotonic_ns()
    return wall, wall - (left + right) // 2, (right - left + 1) // 2


def send(args):
    packets = json.loads(Path(args.packets).read_text())
    raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    raw.bind((args.iface, 0))
    start = time.monotonic_ns()
    with open(args.events, 'w') as handle:
        for packet in packets:
            deadline = start + packet['offset_ns']
            while True:
                remaining = deadline - time.monotonic_ns()
                if remaining <= 0:
                    break
                time.sleep(min(remaining / 1e9, 0.001))
            before, offset, uncertainty = clock_pair()
            length = raw.send(bytes.fromhex(packet['frame_hex']))
            after = time.time_ns()
            handle.write(json.dumps({'packet_id': packet['packet_id'], 'send_before_ns': before,
                                     'send_after_ns': after, 'sent_length': length,
                                     'wall_minus_monotonic_ns': offset,
                                     'clock_pair_uncertainty_ns': uncertainty,
                                     'schedule_lateness_ns': max(0, time.monotonic_ns() - deadline)}) + '\n')
    raw.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    capture = sub.add_parser('capture')
    for name in ['iface', 'pcap', 'events', 'ready', 'stop']:
        capture.add_argument('--' + name, required=True)
    capture.add_argument('--serve', action='store_true')
    tx = sub.add_parser('send')
    for name in ['iface', 'packets', 'events']:
        tx.add_argument('--' + name, required=True)
    args = parser.parse_args()
    {'capture': packet_io.capture, 'send': send}[args.mode](args)
