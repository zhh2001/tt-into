"""按声明的源接口发送并发流，实际身份不依赖离线包编号。"""
import argparse
import json
from pathlib import Path
import socket
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ifaces', nargs='+', required=True)
    p.add_argument('--packets', type=Path, required=True)
    p.add_argument('--events', type=Path, required=True)
    p.add_argument('--start-ns', type=int, required=True)
    a = p.parse_args(); sockets = []
    packets = json.loads(a.packets.read_text())
    if any(type(v['source_interface_index']) is not int or not 0 <= v['source_interface_index'] < len(a.ifaces) for v in packets):
        raise ValueError('源接口映射错误')
    try:
        for iface in a.ifaces:
            channel = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
            channel.bind((iface, 0)); sockets.append(channel)
        with a.events.open('w') as events:
            for packet in packets:
                deadline = a.start_ns + packet['offset_ns']
                while (left := deadline - time.monotonic_ns()) > 0:
                    time.sleep(min(left / 1e9, .001))
                before = time.monotonic_ns(); index = packet['source_interface_index']
                size = sockets[index].send(bytes.fromhex(packet['frame_hex']))
                after = time.monotonic_ns()
                events.write(json.dumps(dict(packet_id=packet['packet_id'], flow=packet['flow'],
                    interface=a.ifaces[index], source_interface_index=index,
                    send_before_monotonic_ns=before, send_after_monotonic_ns=after, sent_length=size,
                    schedule_lateness_ns=max(0, before - deadline))) + '\n')
                events.flush()
    finally:
        for channel in sockets: channel.close()


if __name__ == '__main__':
    main()
