"""固定双入口合流链的节点、并发流、寄存器和共享任务合同。"""
from dataclasses import dataclass
from copy import deepcopy

from orchestration.event_schema import FlowId, ObjectId


@dataclass(frozen=True)
class FixedNetwork:
    record: dict

    def __post_init__(self):
        object.__setattr__(self, 'record', deepcopy(self.record))
        r = self.record
        n = r['sampling_nodes']
        if r.get('family') != 'two_entry_merge_chain' or type(n) is not int or not 3 <= n <= 8 or type(r['sink']) is not int or r['sink'] != n + 1:
            raise ValueError('双入口合流链须有3至8采样节点及独立sink')
        flows = r['flows']
        if not flows or len(flows) > 256:
            raise ValueError('流数超过寄存器/流表容量')
        identities, slots, sports = set(), set(), set()
        for f in flows:
            identity = FlowId(**f['identity'])
            slot, sport, entry = f['slot'], f['sport'], f['entry_node']
            if type(slot) is not int or not 0 <= slot < 256 or type(sport) is not int or not 1 <= sport <= 65535:
                raise ValueError('流槽或UDP源端口越界')
            if identity in identities or slot in slots or sport in sports:
                raise ValueError('身份、寄存器槽或五元组重复')
            if type(entry) is not int or entry not in (1, 2) or type(f['source_interface_index']) is not int or f['source_interface_index'] != entry - 1:
                raise ValueError('流入口与发送接口不符')
            if any(type(node) is not int for node in f['path']) or f['path'] != [entry, *range(3, n + 1)]:
                raise ValueError('业务路径不属于实际无环合流链')
            identities.add(identity); slots.add(slot); sports.add(sport)
        if {f['entry_node'] for f in flows} != {1, 2}:
            raise ValueError('两条入口分支都需要业务载体')

    @property
    def routes(self):
        return {FlowId(**f['identity']): tuple(ObjectId(n) for n in f['path']) for f in self.record['flows']}

    @property
    def slots(self):
        return {FlowId(**f['identity']): f['slot'] for f in self.record['flows']}

    @property
    def count_locations(self):
        return {FlowId(**f['identity']): (f['entry_node'], f['slot']) for f in self.record['flows']}

    @property
    def links(self):
        return {ObjectId(n): f'e{n}' for n in range(1, self.record['sampling_nodes'] + 1)}

    @property
    def nodes(self):
        return tuple(range(1, self.record['sink'] + 1))

    @property
    def keys(self):
        return tuple((o.node, f) for f in sorted(self.routes) for o in self.routes[f])

    @property
    def physical_links(self):
        n, sink = self.record['sampling_nodes'], self.record['sink']
        return (('source', 0, 1, 1), ('source', 1, 2, 1), ('source', 2, 3, 4),
                (1, 2, 3, 1), (2, 2, 3, 3),
                *((node, 2, node + 1, 1) for node in range(3, n + 1)),
                (sink, 2, 'destination', 0), (sink, 3, 'collector', 0))


def fork_chain(nodes=3, flows=2, legacy=False):
    if type(flows) is not int or not 2 <= flows <= 8:
        raise ValueError('首轮固定合流链支持2至8并发流')
    slots = list(range(1, flows + 1)) if legacy else [1, 7, 42, 250, 2, 3, 4, 5][:flows]
    return FixedNetwork(dict(family='two_entry_merge_chain', sampling_nodes=nodes, sink=nodes + 1,
        flows=[dict(identity=dict(session=20, tag=1001 * (i + 1)), slot=slots[i], sport=10100 + slots[i],
                    entry_node=1 + i % 2, source_interface_index=i % 2,
                    path=[1 + i % 2, *range(3, nodes + 1)]) for i in range(flows)]))
