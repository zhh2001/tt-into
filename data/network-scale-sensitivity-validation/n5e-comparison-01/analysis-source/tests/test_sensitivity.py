"""预算/周期适配、因果槽映射和独立敏感性配对破坏检查。"""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'experiments')]
from sensitivity_execution import definition, validate, read_scope, condition_budget
from sensitivity_pairs import records, complete
from check_sensitivity_pairs import check_records
from check_sensitivity_case import check_condition
from collector.observer_schedule import ObserverSchedule
from orchestration.network_spec import FixedNetwork


def audit():
    return dict(selected_fresh_seeds=[3025,3026,3027,3028], excluded_seeds=[11,29,*range(3001,3025)])


def fixture():
    plan = definition('comparison', audit()); summary, sig, ds = [], {}, {}
    for batch in plan['batches']:
        for e in batch['entries']:
            row = {k:e[k] for k in ('condition_id','business_seed','block','method')}
            row.update(batch_id=batch['batch_id'], case=e['name'], window_feasibility='within',
                controller_loss=[1.,1.2], collector_loss=[.5,.6], fee_rates={'export':[100,100],'api':[0,0]},
                weighted_active_interval_coverage=1.)
            summary.append(row); name=row['batch_id']+'/'+row['case']; sig[name]=[[1,1000000,1]]; ds[name]=[]
    return plan, summary, sig, ds


def condition_fixture(period):
    scope, topology = read_scope()
    condition = next(c for c in scope['conditions'] if c['observation_period_us']==period and c['data_budget_multiplier']==1)
    budget = condition_budget(FixedNetwork(topology),1)
    case = dict(scope['cell'], **condition, method='phase_guard',topology=topology,budget=budget,duration_us=10000000,decision_period_us=1000000)
    settings = dict(budget=budget,adaptive=scope['method_parameters']['phase_guard']['adaptive'],notice_budget_bytes_per_s=128,
                    decision_period_us=1000000,observation_period_us=period)
    schedule = ObserverSchedule(1000000000,observation_us=period)
    observations=[schedule.slot(i,schedule.origin_ns+i*period*1000) for i in range(schedule.slots)]
    decisions=[dict(scheduled_offset_us=i*1000000) for i in range(10)]
    return case,settings,observations,decisions


class SensitivityTests(unittest.TestCase):
    def test_formal_complete_denominators(self):
        p=definition('comparison',audit());validate(p,True)
        self.assertEqual((p['expected_cases'],len(p['batches']),p['expected_observation_slots']),(80,2,5600))

    def test_smoke_only_consumed_training_and_endpoints(self):
        p=definition('smoke',audit());validate(p,True)
        self.assertEqual((p['business_seeds'],p['expected_cases'],p['expected_observation_slots']),([11],4,220))
        self.assertEqual({e['condition_id'] for e in p['execution_order']},{'B085','O1000'})

    def test_data_multiplier_preserves_control_budget(self):
        _,topology=read_scope(); spec=FixedNetwork(topology)
        b=condition_budget(spec,.85)
        self.assertEqual((b['link_bytes_per_s']['e1'],b['link_bytes_per_s']['e3'],b['export_bytes_per_s'],b['control_bytes_per_s']),(408.,663.,1530.,1200))

    def test_consumed_business_rejected(self):
        a=audit();a['selected_fresh_seeds'][0]=3021
        with self.assertRaises(ValueError):definition('comparison',a)

    def test_fixed_config_change_rejected(self):
        p=definition('comparison',audit());p['execution_order'][0]['initial_config'][0]=50000
        with self.assertRaises(ValueError):validate(p)

    def test_period_change_rejected_before_business(self):
        p=definition('comparison',audit());p['execution_order'][0]['observation_period_us']=50000
        with self.assertRaises(ValueError):validate(p)

    def test_source_inventory_change_rejected(self):
        p=definition('comparison',audit());p['source_sha256'][next(iter(p['source_sha256']))]='0'*64
        with self.assertRaises(ValueError):validate(p,True)

    def test_all_three_real_periods_and_decision_mapping(self):
        for period in (100000,250000,1000000):
            with self.subTest(period=period):self.assertGreater(check_condition(*condition_fixture(period)),10)

    def test_old_hundred_slot_assumption_rejected(self):
        c,s,o,d=condition_fixture(1000000);o.extend(copy.deepcopy(o)*9)
        with self.assertRaises(ValueError):check_condition(c,s,o,d)

    def test_extra_tick_decision_rejected(self):
        c,s,o,d=condition_fixture(250000);d.append(dict(scheduled_offset_us=250000))
        with self.assertRaises(ValueError):check_condition(c,s,o,d)

    def test_only_data_budget_scaling_rejected_if_api_scaled(self):
        c,s,o,d=condition_fixture(100000);s=copy.deepcopy(s);s['budget']['control_bytes_per_s']=1020
        with self.assertRaises(ValueError):check_condition(c,s,o,d)

    def test_pairing_complete_and_independently_checked(self):
        p,s,sig,ds=fixture();data=records(s,sig,ds,p)
        self.assertGreater(check_records(s,p,data,sig,ds),3000)
        self.assertEqual([len(data[k]) for k in ('paired_cases','business_pairs','paired_groups','deployment_repeats','condition_means')],[104,52,13,40,10])

    def test_missing_condition_is_not_removed_from_denominator(self):
        p,s,_,_=fixture();s.pop()
        with self.assertRaises(ValueError):complete(s,p)

    def test_unavailable_fee_propagates_without_removing_pair(self):
        p,s,sig,ds=fixture();r=s[0];r['fee_rates']['export']=[None,None];r['window_feasibility']='unavailable'
        data=records(s,sig,ds,p);check_records(s,p,data,sig,ds)
        point=next(x for x in data['condition_means'] if (x['condition_id'],x['method'])==(r['condition_id'],r['method']))
        self.assertEqual(point['mean_fee_rates']['export'],[None,None]);self.assertEqual(point['within'],7)

    def test_changed_center_pointer_rejected(self):
        p,s,sig,ds=fixture();data=records(s,sig,ds,p)
        pair=next(x for x in data['paired_cases'] if x['relation']=='condition');pair['right_case']=pair['left_case']
        with self.assertRaises(ValueError):check_records(s,p,data,sig,ds)

    def test_changed_group_mean_rejected(self):
        p,s,sig,ds=fixture();data=records(s,sig,ds,p);data['paired_groups'][0]['controller_mean_left_bounds'][0]=42
        with self.assertRaises(ValueError):check_records(s,p,data,sig,ds)


if __name__=='__main__':unittest.main()
