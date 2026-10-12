"""规模训练业务，保持前两流时间位置及共享节点任务分母。"""
import random
from scalable_workload import packet

SCENARIOS=('stable','shared_priority')


def schedule(spec,seed):
    rng=random.Random(seed)
    times=[]
    for ordinal in range(1,len(spec.record['flows'])+1):
        for start,end in ((0,5000000),(5000000,10000000)):
            t=start+rng.uniform(.1,.9)*125000
            while t<end:
                times.append((int(t),ordinal));t+=125000
    return sorted(times)


def demands(spec,scenario):
    if scenario not in SCENARIOS:raise ValueError('规模训练场景错误')
    priority=scenario=='shared_priority'
    terminal=spec.record['sampling_nodes']
    return [dict(node=n,target_us=(100000 if priority else 150000) if n==terminal else (500000 if priority else 300000),
                 weight=(4.0 if priority else 2.0) if n==terminal else 1.0,active=True)
            for n in range(1,terminal+1)]


def inputs(spec,scenario,seed,base=0):
    ps=[packet(spec,ordinal,base+i,t) for i,(t,ordinal) in enumerate(schedule(spec,seed))]
    ds=demands(spec,scenario)
    notices=[dict(offset_us=0,message=dict(version=1,demands=ds))]
    tasks=[dict(task_id=f"n{d['node']}",object_id=dict(node=d['node'],port=2,queue=0),
                start_us=2000000,end_us=10000000,target_us=d['target_us'],weight=d['weight']) for d in ds]
    return ps,notices,tasks
