"""记录主机资源快照，不记录主机名或用户路径。"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import time


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists(): raise RuntimeError('快照文件已存在')
    cpu=next((line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')),'unknown')
    mem={line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:','MemAvailable:'))}
    data={'recorded_utc':datetime.now(timezone.utc).isoformat(),'recorded_monotonic_ns':time.monotonic_ns(),
          'cpu_model':cpu,'logical_cpus':os.cpu_count(),'memory_kib':mem,'load_average':list(os.getloadavg()),
          'kernel_release':os.uname().release,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          'scope':'本阶段主机资源快照，逐次运行开始与结束的负载见运行清单'}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
