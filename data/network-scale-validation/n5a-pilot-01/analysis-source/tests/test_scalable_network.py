"""通用入口的容量、身份、映射、未知任务与多方法兼容检查。"""
import copy
from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'experiments')]
from orchestration.network_spec import FixedNetwork, fork_chain
from orchestration.event_schema import FlowId, ObjectId, Demand
from orchestration.event_observation import OnlineView, Confirmation, CountObservation, DemandNotification
from orchestration.rolling import Budget
from orchestration.adaptive import AdaptiveSettings
from orchestration.control_ledger import ControlSnapshot
from orchestration.literature import LiteratureSettings
from collector.into_state import IntoState
from collector.tagged_frame import decode_frame
from control.matched_live import LivePlanner
from control.scalable_literature_live import LiteraturePlanner
from rolling_workloads import MANAGEMENT
from scalable_workload import packet, inputs
from scalable_adaptive_check import header_forecast
from check_rolling_reference import clipped_loss


class ScalableNetworkTests(unittest.TestCase):
    def malformed(self, change):
        r = copy.deepcopy(fork_chain(5, 4).record); change(r)
        with self.assertRaises(ValueError): FixedNetwork(r)

    def test_legacy_packet_and_routes_equivalent(self):
        from run_into_validation import packet as old_packet, ROUTES, SLOTS
        spec = fork_chain(3, 2, True)
        self.assertEqual(spec.routes, ROUTES); self.assertEqual(spec.slots, SLOTS)
        for flow in (1, 2):
            for stale in (False, True):
                if stale and flow != 1: continue
                p = packet(spec, flow, 7, 100, 'old_session' if stale else None)
                self.assertEqual(p['frame_hex'], old_packet(7, flow, 100, stale=stale)['frame_hex'])

    def test_legacy_physical_ports_equivalent(self):
        self.assertEqual(fork_chain(3, 2, True).physical_links,
            (('source',0,1,1), ('source',1,2,1), ('source',2,3,4), (1,2,3,1), (2,2,3,3), (3,2,4,1), (4,2,'destination',0), (4,3,'collector',0)))

    def test_source_location_is_not_slot_node(self):
        spec = fork_chain(5, 4)
        self.assertEqual(list(spec.count_locations.values()), [(1,1),(2,7),(1,42),(2,250)])
        self.assertEqual(len(spec.keys), 16)

    def test_eight_samplers_separate_sink_nine(self):
        spec = fork_chain(8, 8)
        self.assertEqual(spec.nodes, tuple(range(1, 10)))
        self.assertEqual(len(spec.links), 8)
        self.assertEqual(max(o.node for r in spec.routes.values() for o in r), 8)
        self.assertEqual(len(spec.keys), 56)

    def test_slot_255_accepted(self):
        r = fork_chain(5,4).record; r['flows'][-1]['slot'] = 255
        self.assertEqual(list(FixedNetwork(r).slots.values())[-1], 255)

    def test_slot_256_rejected(self): self.malformed(lambda r: r['flows'][-1].update(slot=256))
    def test_duplicate_slot_rejected(self): self.malformed(lambda r: r['flows'][-1].update(slot=1))
    def test_duplicate_identity_rejected(self): self.malformed(lambda r: r['flows'][-1].update(identity=r['flows'][0]['identity']))
    def test_duplicate_five_tuple_rejected(self): self.malformed(lambda r: r['flows'][-1].update(sport=r['flows'][0]['sport']))
    def test_node_nine_sampler_rejected(self): self.malformed(lambda r: r.update(sampling_nodes=9,sink=10))
    def test_loop_or_wrong_path_rejected(self): self.malformed(lambda r: r['flows'][0].update(path=[1,3,4,3,5]))
    def test_wrong_source_interface_rejected(self): self.malformed(lambda r: r['flows'][1].update(source_interface_index=0))
    def test_boolean_node_rejected(self): self.malformed(lambda r: r['flows'][0].update(entry_node=True))

    def test_caller_record_mutation_does_not_change_spec(self):
        r = fork_chain(5,4).record; spec = FixedNetwork(r); r['flows'][0]['slot'] = 255
        self.assertEqual(spec.slots[FlowId(20,1001)], 1)

    def test_shared_task_denominator_independent_of_flow_count(self):
        tasks2 = inputs(fork_chain(5,2), 'static',0)[2]
        tasks8 = inputs(fork_chain(5,8), 'static',0)[2]
        self.assertEqual(tasks2,tasks8)
        self.assertEqual(clipped_loss(tasks8, [],4)[0], 4)
        self.assertEqual(len(tasks8),10)

    def test_old_session_report_and_outside_route_leave_shared_cache(self):
        spec = fork_chain(5,4); state = IntoState(spec.routes,0)
        f = spec.record['flows'][0]
        def report(session=20,node=1,hop=1,stamp=999):
            return dict(ether_type=0x88b6,session=session,flow_tag=f['identity']['tag'],ip_checksum_ok=True,
                receive_monotonic_ns=1000000,records=[dict(switch_id=node,hop=hop,generated_us=stamp,queue_packets=0)])
        state.ingest([report()],1000000); baseline = copy.deepcopy(state.cache)
        state.ingest([report(session=19,stamp=1001),report(stamp=123),report(node=5,hop=1,stamp=1002)],1000000)
        self.assertEqual(state.cache,baseline); self.assertEqual(len(state.reports),1)
        self.assertEqual({r['reason'] for r in state.ingestion}, {'fresh','unknown_identity_or_session','outside_registered_route','old_record'})

    def test_future_count_not_visible(self):
        state = IntoState(fork_chain(5,4).routes,0)
        state.counts.append(CountObservation('r',FlowId(20,3003),10,20,8))
        self.assertFalse(state.snapshot(19000,[]).counts)
        self.assertEqual(len(state.snapshot(20000,[]).counts),1)

    def test_manual_probes_preserve_identity_and_k(self):
        ps,_,_ = inputs(fork_chain(5,4),'manual',0)
        self.assertEqual(len(ps),323)
        for p in ps:
            if not p['probe']: continue
            r=decode_frame(bytes.fromhex(p['frame_hex']))
            self.assertEqual(r['count'],1); self.assertEqual(r['records'][0]['generated_us'],123)
            self.assertEqual(r['session'],19 if p['probe']=='old_session' else 20)

    def test_forecast_follows_all_eight_links(self):
        spec=fork_chain(8,2); routes={tuple((f.session,f.tag)): [dict(node=o.node) for o in r] for f,r in spec.routes.items()}
        d=dict(time_us=0,rates=[dict(flow=dict(session=f.session,tag=f.tag),rate_pps=8) for f in spec.routes])
        from orchestration.matched_grid import settings
        from dataclasses import asdict
        predicted=header_forecast(d,routes,asdict(settings()),False)
        self.assertEqual(set(predicted),{f'e{n}' for n in range(1,9)})
        self.assertEqual(predicted['e8'],predicted['e3'])
        self.assertEqual(predicted['e3'],2*predicted['e1'])

    def test_six_methods_use_expanded_legal_view_and_limits(self):
        spec=fork_chain(5,4); management=replace(MANAGEMENT,routes=spec.routes,links=spec.links)
        initial=(150000,)*len(spec.keys)
        confirms=tuple(Confirmation(f'{n}-{f.label}-{field}',0,n,f,field,value,1,True)
            for n,f in spec.keys for field,value in [('threshold_us',150000),('enabled',True)])
        counts=tuple(CountObservation(f'{f.label}-{t}',f,t,t,8*t//1000000) for t in (1000000,2000000) for f in spec.routes)
        notice=DemandNotification(0,1,tuple(Demand(o,200000,1,True) for o in spec.links))
        view=OnlineView(3000000,(),counts,confirms,(),(notice,))
        from control.matched_live import OwnControl
        control=OwnControl([],0).snapshot(3000000000)
        budget=Budget({v:4096 for v in spec.links.values()},8192,4096)
        for method in ('static','feedback','phase_joint','phase_guard','intopp_due','d_into_tt'):
            planner = LiteraturePlanner(method,management,budget,AdaptiveSettings(),initial,LiteratureSettings()) if method in ('intopp_due','d_into_tt') else LivePlanner(method,management,budget,AdaptiveSettings(gain_margin=.05 if method=='phase_guard' else 0),initial)
            result,changes=planner.decide(view,control)
            self.assertEqual(len(result['selected']['config']),16)
            self.assertLessEqual(len(result['candidate_scores']),48); self.assertLessEqual(len(changes),6)
            if method=='static': self.assertFalse(changes)

    def test_literature_duplicate_repair_requires_two_paid_write_groups(self):
        from orchestration.scalable_literature import duplicates
        spec=fork_chain(5,4); management=replace(MANAGEMENT,routes=spec.routes,links=spec.links)
        initial=(150000,)*16
        values={(n,f,field):value for n,f in spec.keys for field,value in [('enabled',True),('threshold_us',150000)]}
        budget=Budget({v:4096 for v in spec.links.values()},8192,4096)
        from control.matched_live import OwnControl
        planner=LiteraturePlanner('d_into_tt',management,budget,AdaptiveSettings(),initial,LiteratureSettings())
        counts=tuple(CountObservation(f'{f.label}-{t}',f,t,t,8*t//1000000) for t in (1000000,2000000) for f in spec.routes)
        notice=DemandNotification(0,1,tuple(Demand(o,200000,1,True) for o in spec.links))
        previous=9
        for epoch in (3,4):
            confirmations=tuple(Confirmation(str(i),0,n,f,field,value,epoch,True) for i,((n,f,field),value) in enumerate(values.items()))
            view=OnlineView(epoch*1000000,(),counts,confirmations,(),(notice,))
            result,changes=planner.decide(view,OwnControl([],0).snapshot(epoch*1000000000))
            count=duplicates(result['selected']['config'],management.keys)
            self.assertLess(count,previous);previous=count
            self.assertLessEqual(len(changes),6);self.assertLessEqual(len(result['candidate_scores']),48)
            for (n,f),field,value in changes: values[n,f,field]=value
        self.assertEqual(previous,0)
        self.assertGreater(result['literature_state']['settled_change_bytes'],0)

    def test_prepared_matrix_contains_no_new_business_seed(self):
        from scalable_contract import definition,validate
        plan=definition();validate(plan,True)
        self.assertEqual(plan['business_seeds'],[])
        self.assertEqual(len(plan['execution_order']),8)
        self.assertEqual(sum(len(inputs(FixedNetwork(plan['networks'][e['network_id']]),e['method'],i*1000)[0]) for i,e in enumerate(plan['execution_order'])),1926)

    def test_ingress_gate_precedes_report_generation(self):
        from scalable_gate_check import check_final
        self.assertEqual(check_final(1,999,[1000]),2)
        self.assertEqual(check_final(0,0,[]),2)

    def test_inconsistent_gate_state_rejected(self):
        from scalable_gate_check import check_final
        for has,gate,stamps in [(0,0,[1000]),(1,1001,[1000]),(1,0,[1000]),(0,1,[])]:
            with self.assertRaises(ValueError):check_final(has,gate,stamps)


if __name__ == '__main__': unittest.main()
