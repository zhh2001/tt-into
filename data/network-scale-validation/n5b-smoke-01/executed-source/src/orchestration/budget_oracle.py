"""格式与源端封装可选的离线硬期限下界，控制费用由固定账本给定。"""
import math
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix
from orchestration.budget_model import FORMATS, costs, evaluate, ledger_cost, validate


def solve(tasks, packets, threshold_us, start, end, budget=None, fmt=FORMATS['common'],
          source_mode='fixed', control_events=(), gate_scope='flow', initial_gate=None,
          force_all=False, require_mask=False, require_epoch=False, time_limit=20):
    control_events=tuple(control_events)
    validate(tasks,packets,start,end,fmt,budget,control_events,require_mask,require_epoch)
    thresholds=threshold_us if isinstance(threshold_us,dict) else {t.node:threshold_us for t in tasks}
    if source_mode not in ['fixed','optional'] or gate_scope not in ['flow','port'] or any(
        t.node not in thresholds or not math.isfinite(thresholds[t.node]) or thresholds[t.node]<0 for t in tasks):
        raise ValueError('源端选择或门控参数不合法')
    links=len(budget.control_per_link_bytes) if budget else 1
    fixed=ledger_cost(control_events,links)
    answer={'format':fmt.name,'source_mode':source_mode,'gate_scope':gate_scope,
            'future_packets_known':True,'control_cost':fixed,
            'deployment_feasibility_established':False,
            'scope':'共同链、固定资格与延迟/丢失、无替换的逐包选择下界；账本不证明这些权限可免费安装'}
    if budget and any(v>b for v,b in zip(fixed['control_per_link_bytes'],budget.control_per_link_bytes)):
        return {**answer,'status':'infeasible','reason':'预声明控制账本已超预算'}
    n,h=len(packets),len(tasks)
    source=lambda p:n*h+p
    report=lambda p:n*h+n+p
    export=lambda p:n*h+2*n+p
    accept=lambda j:n*h+3*n+j
    count=n*h+3*n+h
    lo,hi=np.zeros(count),np.ones(count)
    integrality=np.ones(count)
    rows,lowers,uppers=[],[],[]
    objective_cost=np.zeros(count)
    def add(coefficients,lower=-np.inf,upper=np.inf):
        rows.append(coefficients);lowers.append(lower);uppers.append(upper)
    max_export=max(fmt.export_minimum_bytes,34+fmt.header_bytes+fmt.record_bytes*h)
    for p,packet in enumerate(packets):
        hi[source(p)]=int(packet.envelope(fmt))
        if source_mode=='fixed': lo[source(p)]=hi[source(p)]
        hi[export(p)]=max_export
        ys={p*h+j:1 for j in range(h)}
        add(ys,upper=packet.slots(fmt))
        add({report(p):1,**{k:-v for k,v in ys.items()}},upper=0)
        for j,t in enumerate(tasks):
            y=p*h+j
            add({y:1,source(p):-1},upper=0)
            add({y:1,report(p):-1},upper=0)
            add({y:1,accept(j):-1},upper=0)
            if not packet.eligible(t.node): hi[y]=0
            key=(t.node,packet.flow if gate_scope=='flow' else 0)
            if key in (initial_gate or {}) and packet.gate_us[packet.path.index(t.node)]-initial_gate[key]<thresholds[t.node]:
                hi[y]=0
        emitted=int(packet.export_emitted)
        base=34+fmt.header_bytes
        add({export(p):1,report(p):-base*emitted,**{k:-fmt.record_bytes*emitted for k in ys}},lower=0)
        add({export(p):1,report(p):-fmt.export_minimum_bytes*emitted},lower=0)
        add({export(p):1,report(p):-max_export*emitted},upper=0)
        objective_cost[source(p)]=fmt.header_bytes*h
        objective_cost[export(p)]=1
    checks=sorted({start,end,*(p.arrival_us for p in packets if p.arrival_us is not None and start<p.arrival_us<=end)})
    for j,t in enumerate(tasks):
        for p,packet in enumerate(packets):
            i=packet.path.index(t.node)
            for q in range(p+1,n):
                other=packets[q]
                if gate_scope=='flow' and packet.flow!=other.flow: continue
                if abs(packet.gate_us[i]-other.gate_us[i])<thresholds[t.node]:
                    add({p*h+j:1,q*h+j:1},upper=1)
        for moment in checks:
            fresh=t.initial_generated_us is not None and t.initial_generated_us>=moment-t.deadline_us
            coefficients={accept(j):-1}
            for p,packet in enumerate(packets):
                received=packet.arrival_us is not None and (packet.arrival_us<=moment if moment==start else packet.arrival_us<moment)
                if received and packet.generated_us[packet.path.index(t.node)]>=moment-t.deadline_us:
                    coefficients[p*h+j]=1
            add(coefficients,lower=-int(fresh))
        if force_all: lo[accept(j)]=1
    for edge in range(h):
        coefficients={source(p):fmt.header_bytes for p in range(n)}
        for p,packet in enumerate(packets):
            for j,t in enumerate(tasks):
                if packet.path.index(t.node)<=edge:
                    coefficients[p*h+j]=fmt.record_bytes
                    objective_cost[p*h+j]+=fmt.record_bytes
        if budget: add(coefficients,upper=budget.inband_per_link_bytes[edge])
    if budget: add({export(p):1 for p in range(n)},upper=budget.export_bytes)
    def constraints():
        rr,cc,v=[],[],[]
        for r,coefficients in enumerate(rows):
            for c,value in coefficients.items(): rr.append(r);cc.append(c);v.append(value)
        matrix=coo_matrix((v,(rr,cc)),shape=(len(rows),count)).tocsc()
        return LinearConstraint(matrix,lowers,uppers)
    objective=np.zeros(count)
    for j,t in enumerate(tasks): objective[accept(j)]=-t.weight
    def optimize(obj):
        return milp(obj,integrality=integrality,bounds=Bounds(lo,hi),constraints=constraints(),
                    options={'time_limit':time_limit,'mip_rel_gap':0})
    first=optimize(objective)
    answer.update(status='optimal' if first.status==0 else 'infeasible' if first.status==2 else 'limited',
                  solver_status=int(first.status),message=first.message)
    if first.x is None: return answer
    weight=sum(t.weight*round(first.x[accept(j)]) for j,t in enumerate(tasks))
    solution=first
    if first.status==0:
        add({accept(j):t.weight for j,t in enumerate(tasks)},lower=weight,upper=weight)
        second=optimize(objective_cost)
        answer['cost_solver_status']=int(second.status)
        if second.x is not None: solution=second
    selected={packet.packet_id:[t.node for j,t in enumerate(tasks) if solution.x[p*h+j]>0.5] for p,packet in enumerate(packets)}
    enabled={packet.packet_id:bool(solution.x[source(p)]>0.5) for p,packet in enumerate(packets)}
    accepted=[t.node for j,t in enumerate(tasks) if solution.x[accept(j)]>0.5]
    actual=costs(packets,selected,fmt,enabled,control_events,links)
    metrics=evaluate(tasks,packets,selected,start,end)
    # 连续年龄积分、格式字节与门控间隔独立核查求解器返回值。
    assert all(r['violation_fraction']<=1e-8 for r in metrics['tasks'] if r['node'] in accepted)
    for t in tasks:
        gates={}
        for packet in packets:
            if t.node in selected[packet.packet_id]:
                key=packet.flow if gate_scope=='flow' else 0
                gates.setdefault(key,[]).append(packet.gate_us[packet.path.index(t.node)])
        assert all(all(b-a>=thresholds[t.node]-1e-8 for a,b in zip(sorted(ts),sorted(ts)[1:])) for ts in gates.values())
    if budget:
        assert all(v<=b for v,b in zip(actual['inband_per_link_bytes'],budget.inband_per_link_bytes))
        assert actual['export_bytes']<=budget.export_bytes
        assert all(v<=b for v,b in zip(actual['control_per_link_bytes'],budget.control_per_link_bytes))
    answer.update(selected=selected,source_selected=enabled,accepted_nodes=accepted,
                  admitted_weight=weight,cost=actual,metrics=metrics,
                  cost_optimal=answer.get('cost_solver_status')==0)
    return answer
