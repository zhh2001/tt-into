"""额外的抖动与无通知场景，算法不会收到场景名称。"""
from dataclasses import replace
import random

from orchestration.event_schema import Hop, Packet
from rolling_workloads import MANAGEMENT, SCENARIOS as ORIGINAL, Workload, generate as original

SCENARIOS = ORIGINAL + ('bursty_jitter', 'unannounced_switch')
TRAIN_SEEDS = (11, 29)
CALIBRATION_SEEDS = (37, 53)
TEST_SEEDS = (503, 607, 709, 809)


def generate(scenario, seed):
    if scenario in ORIGINAL:
        return original(scenario, seed)
    if scenario == 'unannounced_switch':
        template = original('stable', seed)
        return replace(template, scenario=scenario, packets=original('switch', seed).packets)
    if scenario != 'bursty_jitter':
        raise ValueError('未知场景')
    rng = random.Random(seed)
    entries = []
    for flow in sorted(MANAGEMENT.routes):
        phase = rng.randrange(50000, 200000)
        for block in range(0, 10000000, 500000):
            stamp = block + phase + rng.randrange(-30000, 30001)
            for _ in range(4):
                entries.append((stamp, flow))
                stamp += rng.randrange(500, 2001)
    packets = []
    for pid, (stamp, flow) in enumerate(sorted(entries)):
        route = MANAGEMENT.routes[flow]
        hops = tuple(Hop(o, MANAGEMENT.links[o], stamp + i * 20, stamp + i * 20 + 5,
                         queue_packets=rng.randrange(101)) for i, o in enumerate(route))
        sink = hops[-1].egress_us + 5
        packets.append(Packet(pid, flow, hops, sink, (sink + 1000,)))
    template = original('stable', seed)
    return Workload(scenario, seed, tuple(packets), template.tasks, template.notices)
