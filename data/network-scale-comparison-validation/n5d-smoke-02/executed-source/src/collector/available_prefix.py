"""记录实际读到的完整文件前缀及进入合法状态的时间上界。"""
import hashlib
import json
import time


def ingest_prefix(state, path, clock=None):
    clock = time.monotonic_ns if clock is None else clock
    started = clock()
    raw = path.read_bytes()
    complete = raw[:raw.rfind(b'\n') + 1]
    rows = [json.loads(line) for line in complete.splitlines() if line]
    read_finished = clock()
    if read_finished < started or any(r.get('receive_monotonic_ns', read_finished) > read_finished for r in rows):
        raise ValueError('读取时序倒序或文件包含未来接收记录')
    ingest_started = clock()
    if ingest_started < read_finished:
        raise ValueError('状态更新先于读取完成')
    before = len(state.reports)
    state.ingest(rows, ingest_started)
    available = clock()
    if available < ingest_started:
        raise ValueError('状态更新时间倒序')
    return {'read_started_ns': started, 'read_finished_ns': read_finished,
            'ingest_started_ns': ingest_started, 'available_ns': available,
            'file_bytes': len(raw), 'prefix_bytes': len(complete), 'row_count': len(rows),
            'prefix_sha256': hashlib.sha256(complete).hexdigest(),
            'reports_before': before, 'reports_after': len(state.reports)}
