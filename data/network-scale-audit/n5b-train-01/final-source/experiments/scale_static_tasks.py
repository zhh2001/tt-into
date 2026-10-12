"""不导入业务生成器，独立核对节点任务、初始需求及静态合同。"""
from check_rolling_reference import same


def check_tasks(topology,scenario,tasks,notices):
    if scenario not in ('stable','shared_priority'):raise ValueError('未声明静态场景')
    last=topology['sampling_nodes'];priority=scenario=='shared_priority'
    expected=[];demands=[]
    for node in range(1,last+1):
        target=(100000 if priority else 150000) if node==last else (500000 if priority else 300000)
        weight=(4.0 if priority else 2.0) if node==last else 1.0
        expected.append(dict(task_id=f'n{node}',object_id=dict(node=node,port=2,queue=0),
            start_us=2000000,end_us=10000000,target_us=target,weight=weight))
        demands.append(dict(node=node,target_us=target,weight=weight,active=True))
    same(tasks,expected,'真实共享节点单区间任务及目标/权重')
    same(notices,[dict(offset_us=0,message=dict(version=1,demands=demands))],'唯一初始合法需求合同')
    return last+1
