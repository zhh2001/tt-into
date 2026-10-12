"""固定包轨迹中的共同任务、容量与逐链路成本。时间单位为微秒。"""
from __future__ import annotations
from dataclasses import dataclass

HEADER_BYTES = 8
RECORD_BYTES = 20
EXPORT_FIXED_BYTES = 34 + HEADER_BYTES


@dataclass(frozen=True)
class Task:
    node: int
    deadline_us: int
    weight: float = 1.0
    epsilon: float = 0.0
    initial_generated_us: float | None = None


@dataclass(frozen=True)
class Packet:
    packet_id: int
    flow: int
    path: tuple[int, ...]
    gate_us: tuple[float, ...]
    generated_us: tuple[float, ...]
    arrival_us: float
    ip_bytes: int = 120
    capacity: int = 1

    def slots(self):
        return max(0, min(self.capacity, len(self.path), (1500 - self.ip_bytes - HEADER_BYTES) // RECORD_BYTES))

    def envelope(self):
        return self.ip_bytes + HEADER_BYTES <= 1500


def costs(packets, selected):
    """权限参照无替换时的附加字节，输出每条采样链路和导出链路。"""
    hops = len(packets[0].path) if packets else 0
    inband = [0] * hops
    export = 0
    for packet in packets:
        chosen = set(selected.get(packet.packet_id, []))
        for edge in range(hops):
            inband[edge] += HEADER_BYTES * packet.envelope()
            inband[edge] += RECORD_BYTES * sum(node in chosen for node in packet.path[:edge + 1])
        if chosen:
            export += EXPORT_FIXED_BYTES + RECORD_BYTES * len(chosen)
    return {'inband_per_link_bytes': inband, 'export_bytes': export,
            'control_bytes_in_window': 0, 'total_byte_hops': sum(inband) + export}


def validate_trace(tasks, packets, start, end):
    nodes = {task.node for task in tasks}
    if not packets or start >= end or len(nodes) != len(tasks):
        raise ValueError('任务或窗口不合法')
    if len({p.packet_id for p in packets}) != len(packets):
        raise ValueError('包标识重复')
    path = packets[0].path
    if set(path) != nodes or len(path) != len(nodes):
        raise ValueError('首版只支持一条共同链，任务与采样节点一一对应')
    for task in tasks:
        if task.deadline_us <= 0 or task.weight <= 0 or not 0 <= task.epsilon <= 1:
            raise ValueError('任务参数不合法')
        if task.initial_generated_us is not None and task.initial_generated_us > start:
            raise ValueError('初始样本不能来自未来')
    for p in packets:
        if p.path != path or len(p.gate_us) != len(path) or len(p.generated_us) != len(path):
            raise ValueError('路径或时间向量不一致')
        if any(g < a for g, a in zip(p.generated_us, p.gate_us)) or any(b < a for a, b in zip(p.generated_us, p.generated_us[1:])) or p.arrival_us < max(p.generated_us):
            raise ValueError('生成与到达因果关系错误')
        if p.ip_bytes < 28 or p.ip_bytes > 1500 or p.capacity < 0:
            raise ValueError('容量参数不合法')
