"""按固定路径在三个接口发包；记录同一宿主单调时钟边界。"""
import argparse
import json
from pathlib import Path
import socket
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ifaces', nargs=3, required=True)
    p.add_argument('--packets', type=Path, required=True)
    p.add_argument('--events', type=Path, required=True)
    p.add_argument('--start-ns', type=int, required=True)
    p.add_argument('--pause-offset-ns', type=int)
    p.add_argument('--pause-ready', type=Path)
    p.add_argument('--resume', type=Path)
    a = p.parse_args()
    if a.pause_offset_ns is not None and (a.pause_ready is None or a.resume is None):
        p.error('暂停需要同时给出屏障与恢复路径')
    sockets = []
    paused = False
    try:
        for iface in a.ifaces:
            channel = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
            channel.bind((iface, 0))
            sockets.append(channel)
        with a.events.open('w') as events:
            for packet in json.loads(a.packets.read_text()):
                if a.pause_offset_ns is not None and packet['offset_ns'] >= a.pause_offset_ns and not paused:
                    a.pause_ready.write_text(json.dumps({'paused_monotonic_ns': time.monotonic_ns()}) + '\n')
                    timeout = time.monotonic() + 5
                    while not a.resume.exists():
                        if time.monotonic() > timeout:
                            raise RuntimeError('控制器未恢复发送进程')
                        time.sleep(0.0002)
                    paused = True
                deadline = a.start_ns + packet['offset_ns']
                while (left := deadline - time.monotonic_ns()) > 0:
                    time.sleep(min(left / 1e9, 0.001))
                before = time.monotonic_ns()
                sent = sockets[packet['flow'] - 1].send(bytes.fromhex(packet['frame_hex']))
                after = time.monotonic_ns()
                events.write(json.dumps({'packet_id': packet['packet_id'], 'flow': packet['flow'],
                                         'interface': a.ifaces[packet['flow'] - 1],
                                         'send_before_monotonic_ns': before, 'send_after_monotonic_ns': after,
                                         'sent_length': sent, 'schedule_lateness_ns': max(0, before - deadline)}) + '\n')
                events.flush()
    finally:
        for channel in sockets:
            channel.close()


if __name__ == '__main__':
    main()
