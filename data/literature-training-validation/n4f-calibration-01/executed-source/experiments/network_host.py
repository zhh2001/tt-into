"""低频记录宿主与本次控制器、交换机 CPU 和常驻内存。"""
import os
from pathlib import Path
import time


def snapshot(switches):
    processes = {'controller': os.getpid(), **{f'switch_{i}': s.process.pid for i, s in enumerate(switches, 1)}}
    stats = {}
    for name, pid in processes.items():
        raw = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        stats[name] = {'cpu_ticks': int(raw[11])+int(raw[12]), 'rss_bytes': int(raw[21])*os.sysconf('SC_PAGE_SIZE')}
    mem = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        if key in ('MemTotal', 'MemAvailable'):
            mem[key+'_bytes'] = int(value.split()[0])*1024
    return {'monotonic_ns': time.monotonic_ns(), 'cpu_count': os.cpu_count(), 'load': list(os.getloadavg()),
            'clock_ticks_per_s': os.sysconf('SC_CLK_TCK'), 'memory': mem, 'processes': stats}


def interval(start, end):
    elapsed = (end['monotonic_ns']-start['monotonic_ns'])/1e9
    values = {}
    for name in start['processes']:
        ticks = end['processes'][name]['cpu_ticks']-start['processes'][name]['cpu_ticks']
        if ticks < 0 or elapsed <= 0:
            raise ValueError('宿主观察区间错误')
        seconds = ticks/start['clock_ticks_per_s']
        values[name] = {'cpu_seconds': seconds, 'mean_cpu_cores': seconds/elapsed,
                        'rss_start_bytes': start['processes'][name]['rss_bytes'],
                        'rss_end_bytes': end['processes'][name]['rss_bytes']}
    return {'start': start, 'end': end, 'elapsed_s': elapsed, 'usage': values,
            'scope': '案例安装至核对完成，控制器及四个 BMv2 进程；RSS 端点非峰值，不包括采集/发包子进程 CPU'}
