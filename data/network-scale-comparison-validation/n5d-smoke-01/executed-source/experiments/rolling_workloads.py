"""固定分支配对工作负载和预声明资源预算。"""
from dataclasses import dataclass
import random

from orchestration.event_schema import Demand, FlowId, Hop, Node, Notice, ObjectId, Packet, RpcCost, Task
from orchestration.rolling import Budget, Management

SCENARIOS = ('stable', 'bursty', 'shared_priority', 'switch', 'sparse_stop', 'report_impairment', 'budget_floor')
TRAIN_SEEDS = (11, 29)
TEST_SEEDS = (101, 211, 307, 401)
MEASUREMENT = (2000000, 10000000)
COST = RpcCost(94, 30, '本机阈值写入序列化长度，运行前需与归档 RPC 核对')
ENABLE_COST = RpcCost(89, 30, '本机容量写入序列化长度，运行前需与归档 RPC 核对')
READ_COST = RpcCost(81, 40, '本机源计数读取的序列化长度，不是本轮网络实测')
NOTICE_COST = RpcCost(128, 0, '预声明管理通知消息 128 B，尚无实际管理传输测量')
FLOWS = (FlowId(20, 1001), FlowId(20, 2002))
OBJECTS = (ObjectId(1), ObjectId(2), ObjectId(3))
MANAGEMENT = Management({FLOWS[0]: (OBJECTS[0], OBJECTS[2]), FLOWS[1]: (OBJECTS[1], OBJECTS[2])},
                        {obj: f'e{obj.node}' for obj in OBJECTS}, COST,
                        {'enabled': ENABLE_COST, 'threshold_us': COST}, READ_COST, NOTICE_COST)
NODES = (Node(1, 1000), Node(2, -2000), Node(3, 5000))


@dataclass(frozen=True)
class Workload:
    scenario: str
    seed: int
    packets: tuple
    tasks: tuple
    notices: tuple


def budget_for(scenario, level):
    if level not in ('low', 'high'):
        raise ValueError('预算档位错误')
    if scenario == 'budget_floor' and level == 'low':
        return Budget({'e1': 100, 'e2': 100, 'e3': 150}, 360, 1200)
    if level == 'low':
        return Budget({'e1': 180, 'e2': 180, 'e3': 300}, 540, 1200)
    return Budget({'e1': 240, 'e2': 240, 'e3': 390}, 900, 1200)


def generate(scenario, seed):
    if scenario not in SCENARIOS:
        raise ValueError('未知工作负载')
    rng = random.Random(seed)
    entries = []
    for index, flow in enumerate(FLOWS):
        if scenario == 'bursty':
            phase = rng.randrange(10000, 200000)
            times = [block + phase + j * 1000 for block in range(0, 10000000, 500000) for j in range(4)]
        else:
            times = []
            for start, end in ((0, 5000000), (5000000, 10000000)):
                rate = 2 if scenario == 'sparse_stop' else (14 if index == 0 else 2) if scenario == 'switch' and start == 0 else \
                    (2 if index == 0 else 14) if scenario == 'switch' else 8
                if scenario == 'sparse_stop' and index == 0 and start:
                    continue
                spacing = 1000000 / rate
                phase = rng.uniform(0.1, 0.9) * spacing
                t = start + phase
                while t < end:
                    times.append(int(t))
                    t += spacing
        entries.extend((t, flow) for t in times if t < 10000000)
    entries.sort(key=lambda row: (row[0], row[1]))
    packets = []
    for pid, (time, flow) in enumerate(entries):
        route = MANAGEMENT.routes[flow]
        hops = tuple(Hop(obj, MANAGEMENT.links[obj], time + i * 20, time + i * 20 + 5,
                         queue_packets=rng.randrange(0, 101)) for i, obj in enumerate(route))
        sink = hops[-1].egress_us + 5
        delayed = 250000 if scenario == 'report_impairment' else 1000
        arrived = () if scenario == 'report_impairment' and rng.random() < 0.25 else (sink + delayed,)
        packets.append(Packet(pid, flow, hops, sink, arrived))
    initial_targets = (300000, 300000, 150000)
    initial_weights = (1, 1, 2)
    if scenario == 'shared_priority':
        initial_targets, initial_weights = (500000, 500000, 100000), (1, 1, 4)
    phase_specs = [(0, 10000000, initial_targets, initial_weights)]
    if scenario == 'switch':
        phase_specs = [(0, 5000000, initial_targets, initial_weights),
                       (5000000, 10000000, (600000, 150000, 200000), (1, 3, 2))]
    tasks, notices = [], []
    for version, (start, end, targets, weights) in enumerate(phase_specs, 1):
        notices.append(Notice('demand-' + str(version), start, version,
                              tuple(Demand(obj, target, weight) for obj, target, weight in zip(OBJECTS, targets, weights)), NOTICE_COST))
        for obj, target, weight in zip(OBJECTS, targets, weights):
            if max(start, MEASUREMENT[0]) < end:
                tasks.append(Task(f'task-{version}-{obj.label}', obj, max(start, MEASUREMENT[0]), end, target, weight))
    return Workload(scenario, seed, tuple(packets), tuple(tasks), tuple(notices))
