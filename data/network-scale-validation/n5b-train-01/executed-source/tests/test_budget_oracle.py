"""穷举及手算核对格式、源端选择、丢失和固定控制账本。"""
import itertools
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from orchestration.budget_model import Task,Packet,ControlEvent,Budget,FORMATS,costs,evaluate
from orchestration.budget_oracle import solve


class BudgetTests(unittest.TestCase):
    def test_exact_bytes_and_minimum_export(self):
        p=Packet(1,1,(1,2),(0,1),(0,1),2)
        result=costs([p],{1:[2]},FORMATS['task'])
        self.assertEqual(result['inband_per_link_bytes'],[5,17])
        self.assertEqual(result['export_bytes_without_padding'],51)
        self.assertEqual(result['export_bytes'],60)
        self.assertEqual(result['total_byte_hops'],82)
        self.assertEqual(costs([p],{},FORMATS['task'])['data_total_byte_hops'],10)
        self.assertEqual(costs([p],{},FORMATS['task'],{1:False})['data_total_byte_hops'],0)

    def test_mtu_depends_on_format(self):
        p=Packet(1,1,(1,),(0,),(0,),1,ip_bytes=1479)
        self.assertEqual(p.slots(FORMATS['common']),0)
        self.assertEqual(p.slots(FORMATS['task']),1)

    def test_export_suppression_differs_from_delivery_loss(self):
        emitted=Packet(1,1,(1,),(0,),(0,),None)
        suppressed=Packet(1,1,(1,),(0,),(0,),None,export_emitted=False)
        a=costs([emitted],{1:[1]},FORMATS['task'])
        b=costs([suppressed],{1:[1]},FORMATS['task'])
        self.assertEqual(a['inband_per_link_bytes'],b['inband_per_link_bytes'])
        self.assertEqual(a['export_bytes'],60)
        self.assertEqual(b['export_bytes'],0)

    def test_installation_and_observation_count_before_window(self):
        tasks=[Task(1,10,initial_generated_us=0)]
        packets=[Packet(1,1,(1,),(1,),(1,),2)]
        ledger=[ControlEvent('installation',-5,-1,(8,10),'declared test'),
                ControlEvent('observation',1,2,(7,10),'declared test')]
        result=solve(tasks,packets,0,0,5,Budget((100,),100,(14,20)),control_events=ledger)
        self.assertEqual(result['status'],'infeasible')
        result=solve(tasks,packets,0,0,5,Budget((100,),100,(15,20)),control_events=ledger)
        self.assertEqual(result['accepted_nodes'],[1])
        self.assertEqual(result['cost']['control_per_link_bytes'],[15,20])
        self.assertEqual(result['cost']['total_byte_hops'],43)

    def test_lost_and_late_reports_cannot_refresh(self):
        tasks=[Task(1,5,initial_generated_us=0)]
        for arrival in [None,11]:
            p=Packet(1,1,(1,),(4,),(4,),arrival)
            self.assertEqual(solve(tasks,[p],0,0,10,source_mode='optional')['accepted_nodes'],[])

    def test_eligibility_and_initial_gate_are_enforced(self):
        tasks=[Task(1,5,initial_generated_us=0)]
        unavailable=Packet(1,1,(1,),(4,),(4,),4,eligible_nodes=())
        self.assertEqual(solve(tasks,[unavailable],0,0,8)['accepted_nodes'],[])
        p=Packet(1,1,(1,),(4,),(4,),4)
        self.assertEqual(solve(tasks,[p],5,0,8,initial_gate={(1,1):0})['accepted_nodes'],[])
        self.assertEqual(solve(tasks,[p],4,0,8,initial_gate={(1,1):0})['accepted_nodes'],[1])

    def test_source_omission_can_change_declared_feasibility(self):
        tasks=[Task(1,8.5,2,initial_generated_us=-1),Task(2,12,1,initial_generated_us=-1)]
        packets=[Packet(i+1,1,(1,2),(t,t+.1),(t,t+.1),t+.2) for i,t in enumerate([2,6,10,14])]
        budget=Budget((64,84),186)
        fixed=solve(tasks,packets,0,0,18,budget,source_mode='fixed')
        optional=solve(tasks,packets,0,0,18,budget,source_mode='optional')
        self.assertLess(fixed['admitted_weight'],3)
        self.assertEqual(optional['admitted_weight'],3)
        self.assertEqual(sum(optional['source_selected'].values()),3)
        self.assertFalse(optional['deployment_feasibility_established'])

    def test_required_metadata_rejects_unsupported_format(self):
        with self.assertRaises(ValueError):
            solve([Task(1,5)],[Packet(1,1,(1,),(0,),(0,),1)],0,0,4,
                  fmt=FORMATS['task'],require_epoch=True)

    def test_serialized_rpc_cannot_be_added_to_ethernet_cost(self):
        p=Packet(1,1,(1,),(0,),(0,),1)
        event=ControlEvent('installation',-2,-1,(30,),'measured payload',basis='serialized_rpc')
        result=costs([p],{1:[1]},control_events=[event])
        self.assertIsNone(result['total_byte_hops'])
        self.assertEqual(result['control_per_link_bytes'],[30])

    def test_all_formats_and_source_modes_match_exhaustion(self):
        tasks=[Task(1,8,2,initial_generated_us=-1),Task(2,12,1,initial_generated_us=-1)]
        packets=[Packet(i+1,1,(1,2),(t,t+.1),(t,t+.1),t+.2,
                        eligible_nodes=(1,) if i==0 else (1,2)) for i,t in enumerate([2,6,10,14])]
        budget=Budget((80,100),186)
        for fmt,mode in itertools.product(FORMATS.values(),['fixed','optional']):
            best=(-1,float('inf'))
            options=[0,1,2] if mode=='fixed' else [-1,0,1,2]
            for choices in itertools.product(options,repeat=4):
                if choices[0]==2: continue
                selected={p.packet_id:[v] if v>0 else [] for p,v in zip(packets,choices)}
                enabled={p.packet_id:v!=-1 for p,v in zip(packets,choices)}
                cost=costs(packets,selected,fmt,enabled)
                if any(a>b for a,b in zip(cost['inband_per_link_bytes'],budget.inband_per_link_bytes)) or cost['export_bytes']>budget.export_bytes:
                    continue
                metrics=evaluate(tasks,packets,selected,0,18)
                weight=sum(t.weight for t,r in zip(tasks,metrics['tasks']) if r['fulfilled'])
                if weight>best[0] or weight==best[0] and cost['total_byte_hops']<best[1]:
                    best=(weight,cost['total_byte_hops'])
            result=solve(tasks,packets,0,0,18,budget,fmt,mode)
            self.assertEqual((result['admitted_weight'],result['cost']['total_byte_hops']),best,(fmt.name,mode))
            self.assertTrue(result['cost_optimal'])


if __name__=='__main__': unittest.main()
