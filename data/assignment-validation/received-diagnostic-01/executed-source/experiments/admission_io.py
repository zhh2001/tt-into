"""固定分支逐包发送，在预声明空隙等待控制器完成寄存器更新。"""
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
    p.add_argument('--barriers', type=Path, required=True)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--start-ns', type=int, required=True)
    a = p.parse_args()
    sockets = []
    barriers = json.loads(a.barriers.read_text())
    index = 0
    try:
        for iface in a.ifaces:
            channel = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
            channel.bind((iface, 0))
            sockets.append(channel)
        with a.events.open('w') as events:
            for packet in json.loads(a.packets.read_text()):
                while index < len(barriers) and packet['offset_ns'] >= barriers[index] * 1000:
                    ready = a.directory / f'barrier-{index}.ready'
                    ready.write_text(json.dumps({'paused_monotonic_ns': time.monotonic_ns(),
                                                 'next_packet_id': packet['packet_id']}) + '\n')
                    timeout = time.monotonic() + 5
                    while not (a.directory / f'barrier-{index}.resume').exists():
                        if time.monotonic() >= timeout:
                            raise RuntimeError('控制屏障未解除')
                        time.sleep(0.0002)
                    index += 1
                deadline = a.start_ns + packet['offset_ns']
                while (left := deadline - time.monotonic_ns()) > 0:
                    time.sleep(min(left / 1e9, 0.001))
                before = time.monotonic_ns()
                sent = sockets[packet['flow'] - 1].send(bytes.fromhex(packet['frame_hex']))
                after = time.monotonic_ns()
                events.write(json.dumps({'packet_id': packet['packet_id'], 'flow': packet['flow'],
                                         'send_before_monotonic_ns': before, 'send_after_monotonic_ns': after,
                                         'sent_length': sent, 'schedule_lateness_ns': max(0, before - deadline)}) + '\n')
                events.flush()
        if index != len(barriers):
            raise RuntimeError('未经过全部屏障')
    finally:
        for channel in sockets:
            channel.close()


if __name__ == '__main__':
    main()
