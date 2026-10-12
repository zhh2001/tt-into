"""最大配置的独立文献评分核对及冻结合同破坏检查。"""
import copy
from dataclasses import asdict, replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments')]
from orchestration.network_spec import fork_chain
from orchestration.event_schema import Demand
from orchestration.event_observation import OnlineView, Confirmation, CountObservation, DemandNotification
from orchestration.adaptive import AdaptiveSettings
from orchestration.rolling import Budget
from orchestration.literature import LiteratureSettings
from control.scalable_literature_live import LiteraturePlanner
from control.matched_live import OwnControl
from rolling_workloads import MANAGEMENT
from run_into_validation import plain
from scale_preflight_contract import definition, validate, validate_paths, method_parameters
from check_scale_literature_decisions import check_decisions


def decisions(method, epochs=2):
    spec=fork_chain(8,8)
    management=replace(MANAGEMENT,routes=spec.routes,links=spec.links)
    values={(n,f,field):value for n,f in spec.keys for field,value in [('enabled',True),('threshold_us',150000)]}
    params=method_parameters()[method]
    budget=Budget({v:4096 for v in spec.links.values()},8192,4096)
    planner=LiteraturePlanner(method,management,budget,AdaptiveSettings(**params['adaptive']),
        (150000,)*56,LiteratureSettings(**params['literature']))
    counts=tuple(CountObservation(f'{f.label}-{t}',f,t,t,8*t//1000000) for t in (1000000,2000000) for f in spec.routes)
    notice=DemandNotification(0,1,tuple(Demand(o,200000,1,True) for o in spec.links))
    result=[]
    for epoch in range(2,2+epochs):
        confirms=tuple(Confirmation(str(i),0,n,f,field,value,epoch,True)
            for i,((n,f,field),value) in enumerate(values.items()))
        view=OnlineView(epoch*1000000,(),counts,confirms,(),(notice,))
        record,writes=planner.decide(view,OwnControl([],0).snapshot(epoch*1000000000))
        result.append(plain(dict(record,time_us=view.now_us,view=view)))
        for (n,f),field,value in writes:values[n,f,field]=value
    opts=dict(settings=asdict(planner.options),literature=params['literature'])
    return result,dict(method=method,topology=spec.record),opts


class ScalePreflightTests(unittest.TestCase):
    def test_validated_short_socket_paths(self):
        validate_paths(definition(),ROOT/'data/network-entry-runs/n5b-preflight-02')

    def test_long_socket_path_rejected_before_deployment(self):
        with self.assertRaises(ValueError):validate_paths(definition(),ROOT/('x'*100))

    def test_prepared_maximum_matrix(self):
        plan=definition();validate(plan,True)
        self.assertEqual(len(plan['execution_order']),6)
        self.assertEqual(plan['business_seeds'],[])
        self.assertTrue(all(len(e['initial_config'])==56 for e in plan['execution_order']))

    def test_frozen_method_parameter_mutation_rejected(self):
        plan=definition();plan['execution_order'][-1]['literature']['value_scale']=1
        with self.assertRaises(ValueError):validate(plan)

    def test_frozen_budget_mutation_rejected(self):
        plan=definition();plan['execution_order'][0]['budget']['link_bytes_per_s']['e8']=1
        with self.assertRaises(ValueError):validate(plan)

    def test_intopp_maximum_independent_prediction(self):
        ds,case,opts=decisions('intopp_due')
        self.assertGreater(check_decisions(ds,case,opts),0)

    def test_d_into_confirmation_queue_across_eight_repairs(self):
        ds,case,opts=decisions('d_into_tt',8)
        self.assertGreater(check_decisions(ds,case,opts),0)
        self.assertGreater(ds[1]['literature_state']['settled_change_bytes'],0)
        chosen=ds[-1]['selected']['config']
        self.assertEqual(sum(x is not None for x in chosen),len({n for (n,_),x in zip(fork_chain(8,8).keys,chosen) if x is not None}))

    def corrupt(self, change):
        ds,case,opts=decisions('d_into_tt')
        change(ds)
        with self.assertRaises(ValueError):check_decisions(ds,case,opts)

    def test_inband_last_link_corruption_rejected(self):
        self.corrupt(lambda ds:ds[0]['candidate_scores'][0]['cost']['inband_per_link_bytes'].__setitem__('e8',0))

    def test_shared_benefit_corruption_rejected(self):
        self.corrupt(lambda ds:ds[0]['candidate_scores'][0].__setitem__('information',999))

    def test_queue_settlement_corruption_rejected(self):
        self.corrupt(lambda ds:ds[1]['literature_state'].__setitem__('settled_change_bytes',0))

    def test_candidate_scan_limit_corruption_rejected(self):
        self.corrupt(lambda ds:ds[0]['selected'].__setitem__('generated_candidates',193))

    def test_selected_config_corruption_rejected(self):
        self.corrupt(lambda ds:ds[0]['selected'].__setitem__('config',[None]*56))


if __name__=='__main__':unittest.main()
