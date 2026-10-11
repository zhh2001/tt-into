"""独立管理通知进程，通过本地 UNIX 数据报交付已冻结的需求。"""
import argparse
import json
from pathlib import Path
import socket
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True)
    parser.add_argument('--schedule', type=Path, required=True)
    parser.add_argument('--events', type=Path, required=True)
    parser.add_argument('--start-ns', type=int, required=True)
    args = parser.parse_args()
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel, args.events.open('w') as events:
        for row in json.loads(args.schedule.read_text()):
            deadline = args.start_ns + row['offset_us'] * 1000
            while (left := deadline - time.monotonic_ns()) > 0:
                time.sleep(min(.001, left / 1e9))
            payload = json.dumps(row['message'], separators=(',', ':'), ensure_ascii=False).encode()
            before = time.monotonic_ns()
            size = channel.sendto(payload, args.socket)
            events.write(json.dumps({'version': row['message']['version'], 'before_ns': before,
                                     'after_ns': time.monotonic_ns(), 'payload_bytes': size, 'payload_hex': payload.hex()}) + '\n')
            events.flush()


if __name__ == '__main__':
    main()
