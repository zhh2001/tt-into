"""独立的格式、源端封装与固定控制账本，保留旧参照模型。"""
from dataclasses import dataclass
import math
from orchestration.model import Task
from orchestration.metrics import age_metrics


@dataclass(frozen=True)
class Format:
    name: str
    header_bytes: int
    record_bytes: int
    reservoir: bool = True
    source_mask: bool = True
    epoch: bool = True
    export_minimum_bytes: int = 60

    def export_bytes(self, count):
        return 0 if not count else max(self.export_minimum_bytes,34+self.header_bytes+self.record_bytes*count)


FORMATS = {
    'common': Format('common',8,20),
    'compact': Format('compact',8,12),
    'task': Format('task',5,12,source_mask=False,epoch=False),
    'prefix': Format('prefix',4,12,reservoir=False,source_mask=False,epoch=False),
}


@dataclass(frozen=True)
class Packet:
    packet_id: int
    flow: int
    path: tuple[int,...]
    gate_us: tuple[float,...]
    generated_us: tuple[float,...]
    arrival_us: float | None
    ip_bytes: int = 120
    capacity: int = 1
    eligible_nodes: tuple[int,...] | None = None
    source_permitted: bool = True
    export_emitted: bool = True

    def envelope(self, fmt):
        return self.source_permitted and self.ip_bytes+fmt.header_bytes<=1500

    def slots(self, fmt):
        return max(0,min(self.capacity,len(self.path),(1500-self.ip_bytes-fmt.header_bytes)//fmt.record_bytes)) if self.envelope(fmt) else 0

    def eligible(self, node):
        return self.eligible_nodes is None or node in self.eligible_nodes


@dataclass(frozen=True)
class ControlEvent:
    kind: str
    issued_us: float
    effective_us: float
    bytes_per_link: tuple[int,...]
    provenance: str
    basis: str = 'ethernet_without_fcs'


@dataclass(frozen=True)
class Budget:
    inband_per_link_bytes: tuple[int,...]
    export_bytes: int
    control_per_link_bytes: tuple[int,...] = (0,)


def ledger_cost(events, links):
    groups={kind:[0]*links for kind in ['installation','observation','update','calibration','validation','unattributed']}
    bases={e.basis for e in events}
    if len(bases)>1 or not bases<= {'ethernet_without_fcs','serialized_rpc'}:
        raise ValueError('控制账本不得混合不同层的字节')
    for e in events:
        if e.kind not in groups or len(e.bytes_per_link)!=links or not e.provenance:
            raise ValueError('控制事件类别、链路数或出处不合法')
        if not math.isfinite(e.issued_us) or not math.isfinite(e.effective_us) or e.effective_us<e.issued_us:
            raise ValueError('控制生效时间不合法')
        if any(not isinstance(v,int) or v<0 for v in e.bytes_per_link):
            raise ValueError('控制字节不合法')
        groups[e.kind]=[a+b for a,b in zip(groups[e.kind],e.bytes_per_link)]
    total=[sum(groups[k][i] for k in groups) for i in range(links)]
    return {'control_per_link_bytes':total,'control_by_kind':groups,
            'control_basis':next(iter(bases),'ethernet_without_fcs'),
            'control_contract':'完整预声明账本，含窗口前安装，不由求解器优化或自动估计'}


def costs(packets, selected, fmt=FORMATS['common'], source_selected=None, control_events=(), control_links=1):
    inband=[0]*len(packets[0].path) if packets else []
    export=captured=0
    enabled={p.packet_id:p.envelope(fmt) for p in packets} if source_selected is None else source_selected
    for p in packets:
        chosen=set(selected.get(p.packet_id,[]))
        active=bool(enabled.get(p.packet_id,False))
        if active and not p.envelope(fmt) or chosen and not active:
            raise ValueError('源端封装与所选记录不一致')
        if len(chosen)>p.slots(fmt) or not chosen<=set(p.path) or any(not p.eligible(n) for n in chosen):
            raise ValueError('记录超容量或不合格')
        for edge in range(len(p.path)):
            inband[edge]+=fmt.header_bytes*active+fmt.record_bytes*sum(n in chosen for n in p.path[:edge+1])
        if chosen and p.export_emitted:
            captured+=34+fmt.header_bytes+fmt.record_bytes*len(chosen)
            export+=fmt.export_bytes(len(chosen))
    result={'inband_per_link_bytes':inband,'export_bytes':export,
            'export_bytes_without_padding':captured,
            'data_total_byte_hops':sum(inband)+export}
    result.update(ledger_cost(control_events,control_links))
    result['total_byte_hops']=result['data_total_byte_hops']+sum(result['control_per_link_bytes']) if result['control_basis']=='ethernet_without_fcs' else None
    return result


def evaluate(tasks, packets, selected, start, end):
    samples={t.node:[] for t in tasks}
    for p in packets:
        if p.arrival_us is None: continue
        for i,n in enumerate(p.path):
            if n in selected.get(p.packet_id,[]): samples[n].append((p.generated_us[i],p.arrival_us))
    rows=[age_metrics(t,samples[t.node],start,end) for t in tasks]
    return {'tasks':rows,'fulfilled_task_count':sum(r['fulfilled'] for r in rows),
            'weighted_violation_fraction':sum(t.weight*r['violation_fraction'] for t,r in zip(tasks,rows))/sum(t.weight for t in tasks)}


def validate(tasks, packets, start, end, fmt, budget, control_events, require_mask=False, require_epoch=False):
    if not packets or not tasks or not math.isfinite(start) or not math.isfinite(end) or start>=end:
        raise ValueError('轨迹窗口不合法')
    path=packets[0].path
    if len(path)!=len(set(path)) or set(path)!={t.node for t in tasks} or len(tasks)!=len(path):
        raise ValueError('只支持共同链，节点与任务一一对应')
    if len({p.packet_id for p in packets})!=len(packets): raise ValueError('包标识重复')
    if require_mask and not fmt.source_mask or require_epoch and not fmt.epoch:
        raise ValueError('格式缺少本任务约定必需的许可或版本字段')
    if fmt.header_bytes<1 or fmt.record_bytes<1 or fmt.export_minimum_bytes<0:
        raise ValueError('格式宽度不合法')
    for t in tasks:
        if t.deadline_us<=0 or t.weight<=0 or t.epsilon!=0:
            raise ValueError('当前 MILP 仅支持正期限、正权重与硬期限')
        if t.initial_generated_us is not None and (not math.isfinite(t.initial_generated_us) or t.initial_generated_us>start):
            raise ValueError('初始样本不合法')
    for p in packets:
        if p.path!=path or len(p.gate_us)!=len(path) or len(p.generated_us)!=len(path): raise ValueError('路径或时间维度错误')
        if not all(math.isfinite(t) for t in p.gate_us+p.generated_us): raise ValueError('时间非有限')
        if any(g<a for g,a in zip(p.generated_us,p.gate_us)) or any(b<a for a,b in zip(p.generated_us,p.generated_us[1:])):
            raise ValueError('生成事件因果关系错误')
        if p.arrival_us is not None and (not math.isfinite(p.arrival_us) or p.arrival_us<max(p.generated_us)):
            raise ValueError('报告可用时间早于生成')
        if not p.export_emitted and p.arrival_us is not None:
            raise ValueError('未导出的包不能有报告可用事件')
        if not 28<=p.ip_bytes<=1500 or not isinstance(p.capacity,int) or p.capacity<0: raise ValueError('报文容量不合法')
        if p.eligible_nodes is not None and (len(set(p.eligible_nodes))!=len(p.eligible_nodes) or not set(p.eligible_nodes)<=set(path)):
            raise ValueError('任务资格不合法')
    links=len(budget.control_per_link_bytes) if budget else 1
    ledger_cost(control_events,links)
    if budget:
        values=budget.inband_per_link_bytes+(budget.export_bytes,)+budget.control_per_link_bytes
        if len(budget.inband_per_link_bytes)!=len(path) or not links or any(not isinstance(v,int) or v<0 for v in values):
            raise ValueError('预算维度或字节不合法')
