"""通用事件模型的整数时间、固定路径及共享对象合同。"""
from dataclasses import dataclass
import math


def integer(value, name, minimum=None, maximum=None):
    if type(value) is not int:
        raise ValueError(name + ' 必须为整数')
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(name + ' 超出范围')


@dataclass(frozen=True, order=True)
class FlowId:
    session: int
    tag: int

    def __post_init__(self):
        integer(self.session, '会话', 0, 2**32 - 1)
        integer(self.tag, '流标识', 0, 2**32 - 1)

    @property
    def label(self):
        return f'{self.session}:{self.tag}'


@dataclass(frozen=True, order=True)
class ObjectId:
    node: int
    port: int = 2
    queue: int = 0

    def __post_init__(self):
        integer(self.node, '节点', 1, 255)
        integer(self.port, '出口', 0, 511)
        # 当前 BMv2 原型的共同指标是默认队列占用。
        integer(self.queue, '队列', 0, 0)

    @property
    def label(self):
        return f'{self.node}:{self.port}:{self.queue}:queue_packets'


@dataclass(frozen=True)
class Node:
    node: int
    offset_us: int = 0
    clock_bits: int = 48
    instance: str = 'fixed'

    def __post_init__(self):
        integer(self.node, '节点', 1, 255)
        integer(self.offset_us, '时钟偏移')
        integer(self.clock_bits, '时钟位数', 2, 48)
        if not self.instance:
            raise ValueError('节点实例不能为空')

    def local(self, time_us):
        return (time_us + self.offset_us) % (1 << self.clock_bits)


@dataclass(frozen=True)
class Hop:
    object_id: ObjectId
    link: str
    ingress_us: int
    egress_us: int
    mtu: int = 1500
    queue_packets: int = 0

    def __post_init__(self):
        integer(self.ingress_us, 'ingress 时间')
        integer(self.egress_us, 'egress 时间')
        integer(self.mtu, 'MTU', 28, 65535)
        integer(self.queue_packets, '队列值', 0, 2**32 - 1)
        if not self.link or self.egress_us < self.ingress_us:
            raise ValueError('链路或单跳因果关系错误')


@dataclass(frozen=True)
class Packet:
    packet_id: int                 # 仅用于原始事件与离线核对，不进入在线接口。
    flow: FlowId
    hops: tuple[Hop, ...]
    sink_us: int
    report_arrivals_us: tuple[int, ...]
    ip_bytes: int = 120
    export_emitted: bool = True

    def __post_init__(self):
        integer(self.packet_id, '包标识', 0)
        integer(self.sink_us, 'sink 时间')
        integer(self.ip_bytes, '原始 IPv4 长度', 28, 65535)
        if type(self.export_emitted) is not bool or not self.hops or len(self.hops) > 253:
            raise ValueError('导出开关或路径长度错误')
        if len({h.object_id.node for h in self.hops}) != len(self.hops):
            raise ValueError('首版只支持固定无环路径')
        if any(b.ingress_us < a.egress_us for a, b in zip(self.hops, self.hops[1:])):
            raise ValueError('下游 ingress 早于上游 egress')
        if self.sink_us < self.hops[-1].egress_us:
            raise ValueError('sink 早于最后一个采样节点')
        for time_us in self.report_arrivals_us:
            integer(time_us, '报告到达')
            if time_us < self.sink_us:
                raise ValueError('报告先于导出')
        if not self.export_emitted and self.report_arrivals_us:
            raise ValueError('未导出报告不能送达')


@dataclass(frozen=True)
class Task:
    task_id: str
    object_id: ObjectId
    start_us: int
    end_us: int
    target_us: int
    weight: float = 1.0

    def __post_init__(self):
        integer(self.start_us, '任务开始')
        integer(self.end_us, '任务结束')
        integer(self.target_us, '任务目标间隔', 1)
        if not self.task_id or self.end_us <= self.start_us:
            raise ValueError('任务标识或活跃区间错误')
        if not isinstance(self.weight, (int, float)) or isinstance(self.weight, bool) or not math.isfinite(self.weight) or self.weight <= 0:
            raise ValueError('任务权重必须有限且为正')


@dataclass(frozen=True)
class RpcCost:
    request_bytes: int
    reply_bytes: int
    provenance: str

    def __post_init__(self):
        integer(self.request_bytes, 'RPC 请求字节', 1)
        integer(self.reply_bytes, 'RPC 回复字节', 0)
        if not self.provenance:
            raise ValueError('RPC 费用需要来源')


@dataclass(frozen=True)
class Write:
    write_id: str
    node: int
    flow: FlowId
    field: str
    value: int | bool
    version: int
    issued_us: int
    effective_us: int
    ack_us: int
    cost: RpcCost
    kind: str = 'update'
    success: bool = True

    def __post_init__(self):
        integer(self.node, '写入节点', 1, 255)
        integer(self.version, '策略版本', 0)
        for value in (self.issued_us, self.effective_us, self.ack_us):
            integer(value, '写入时间')
        if not self.write_id or not self.issued_us <= self.effective_us <= self.ack_us:
            raise ValueError('写入标识或因果关系错误')
        if self.field == 'enabled':
            if type(self.value) is not bool:
                raise ValueError('采样许可必须为布尔值')
        elif self.field == 'threshold_us':
            integer(self.value, '阈值', 1, 2**47 - 1)
        else:
            raise ValueError('只支持采样许可和阈值写入')
        if self.kind not in ('installation', 'update') or type(self.success) is not bool:
            raise ValueError('写入类别或结果错误')


@dataclass(frozen=True)
class Read:
    read_id: str
    flow: FlowId
    node: int
    issued_us: int
    snapshot_us: int
    available_us: int
    cost: RpcCost
    success: bool = True

    def __post_init__(self):
        integer(self.node, '读取节点', 1, 255)
        for value in (self.issued_us, self.snapshot_us, self.available_us):
            integer(value, '读取时间')
        if not self.read_id or not self.issued_us <= self.snapshot_us <= self.available_us:
            raise ValueError('读取标识或因果关系错误')
        if type(self.success) is not bool:
            raise ValueError('读取结果必须为布尔值')


@dataclass(frozen=True)
class Demand:
    object_id: ObjectId
    target_us: int
    weight: float = 1.0
    active: bool = True

    def __post_init__(self):
        integer(self.target_us, '需求目标间隔', 1)
        if not isinstance(self.weight, (int, float)) or isinstance(self.weight, bool) or not math.isfinite(self.weight) or self.weight <= 0:
            raise ValueError('需求权重须有限且为正')
        if type(self.active) is not bool:
            raise ValueError('需求活跃标识须为布尔值')


@dataclass(frozen=True)
class Notice:
    notice_id: str
    available_us: int
    version: int
    demands: tuple[Demand, ...]
    cost: RpcCost

    def __post_init__(self):
        integer(self.available_us, '通知到达时间')
        integer(self.version, '需求版本', 0)
        if not self.notice_id or not self.demands or len({d.object_id for d in self.demands}) != len(self.demands):
            raise ValueError('通知或对象重复')
