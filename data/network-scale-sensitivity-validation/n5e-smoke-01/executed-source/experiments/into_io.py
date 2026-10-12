"""版本 6 的实际捕获，复用既有单调计时及原始套接字接口。"""
import argparse
import json
from pathlib import Path
import select
import signal
import socket
import struct
import time
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from collector.tagged_frame import decode_frame
from packet_io import pcap_write


def capture(args):
    raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    raw.bind((args.iface, 0))
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
    raw.setsockopt(socket.SOL_SOCKET, 35, 1)
    raw.setsockopt(socket.SOL_SOCKET, 40, 1)
    sockets = [raw]
    if args.serve:
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(('0.0.0.0', 9000))
        sockets.append(udp)
    alive = True
    def stop(*unused):
        nonlocal alive
        alive = False
    signal.signal(signal.SIGTERM, stop)
    with open(args.pcap, 'wb') as pcap, open(args.events, 'w') as events:
        pcap.write(struct.pack('<IHHIIII', 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        Path(args.ready).write_text('ready\n')
        while alive and not Path(args.stop).exists():
            ready, _, _ = select.select(sockets, [], [], 0.01)
            for channel in ready:
                if channel is raw:
                    before = time.monotonic_ns()
                    frame, anc, _, address = raw.recvmsg(65535, 256)
                    received = time.monotonic_ns()
                    wall = time.time_ns()
                    if address[2] == socket.PACKET_OUTGOING: continue
                    stamp, drops = wall, 0
                    for level, kind, data in anc:
                        if level == socket.SOL_SOCKET and kind == 35:
                            sec, ns = struct.unpack('@ll', data[:16])
                            stamp = sec * 1000000000 + ns
                        if level == socket.SOL_SOCKET and kind == 40:
                            drops = struct.unpack('I', data[:4])[0]
                    pcap_write(pcap, frame, stamp)
                    try: row = decode_frame(frame)
                    except ValueError as exc: row = {'decode_error': str(exc)}
                    if row:
                        row.update(kind='frame', receive_monotonic_ns=received,
                                   receive_before_monotonic_ns=before, kernel_wall_ns=stamp,
                                   wall_minus_monotonic_ns=wall-received, socket_drop_counter=drops)
                        events.write(json.dumps(row) + '\n')
                else:
                    payload, _ = channel.recvfrom(65535)
                    events.write(json.dumps({'kind': 'udp_application', 'payload_hex': payload.hex()}) + '\n')
            events.flush()
            pcap.flush()
    for channel in sockets: channel.close()


def send(args):
    packets = json.loads(Path(args.packets).read_text())
    raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    raw.bind((args.iface, 0))
    Path(args.ready).write_text('ready\n')
    with open(args.events, 'w') as events:
        for p in packets:
            deadline = args.start_ns + p['offset_ns']
            while (left := deadline - time.monotonic_ns()) > 0:
                time.sleep(min(left / 1e9, 0.001))
            before = time.monotonic_ns()
            sent = raw.send(bytes.fromhex(p['frame_hex']))
            after = time.monotonic_ns()
            wall = time.time_ns()
            events.write(json.dumps({'packet_id': p['packet_id'], 'send_before_monotonic_ns': before,
                                     'send_after_monotonic_ns': after, 'sent_length': sent,
                                     'wall_minus_monotonic_ns': wall-after,
                                     'schedule_lateness_ns': max(0, before-deadline)}) + '\n')
            events.flush()
    raw.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='mode', required=True)
    cap = sub.add_parser('capture')
    for n in ['iface','pcap','events','ready','stop']: cap.add_argument('--'+n,required=True)
    cap.add_argument('--serve',action='store_true')
    tx = sub.add_parser('send')
    for n in ['iface','packets','events','ready']: tx.add_argument('--'+n,required=True)
    tx.add_argument('--start-ns',type=int,required=True)
    a=p.parse_args()
    {'capture':capture,'send':send}[a.mode](a)
