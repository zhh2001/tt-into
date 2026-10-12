"""有限训练候选、基础头费用和共享任务的独立核对。"""
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'experiments')]
from orchestration.network_spec import fork_chain
from scale_training_contract import definition,validate,budget_for,foundation,GRID
from scale_training_workload import schedule,demands,inputs
from rolling_workloads import generate


class ScaleTrainingContractTests(unittest.TestCase):
    def test_eight_flows_foundation_and_frozen_ratios(self):
        spec=fork_chain(5,8)
        self.assertEqual(foundation(spec)['unavoidable_link_bytes_per_s'],dict(e1=384,e2=384,e3=768,e4=768,e5=768))
        b=budget_for(spec)
        self.assertEqual(b,dict(link_bytes_per_s=dict(e1=960,e2=960,e3=1560,e4=1560,e5=1560),export_bytes_per_s=3600,control_bytes_per_s=1200))

    def test_two_flows_preserve_prior_high_budget(self):
        self.assertEqual(budget_for(fork_chain(3,2)),dict(link_bytes_per_s=dict(e1=240,e2=240,e3=390),export_bytes_per_s=900,control_bytes_per_s=1200))

    def test_five_finite_pools_unique_in_common_grid(self):
        plan=definition()
        self.assertEqual(len(plan['candidates']),5)
        for pool in plan['candidates'].values():
            self.assertEqual(len(pool),48)
            self.assertEqual(len({tuple(r['config']) for r in pool}),48)
            self.assertTrue(all(v is None or v in GRID for r in pool for v in r['config']))
            self.assertTrue(any(all(v is None for v in r['config']) for r in pool))

    def test_train_only_matrix_no_duplicate_axis_point(self):
        plan=definition();validate(plan,True)
        self.assertEqual(len(plan['execution_order']),960)
        self.assertEqual(len({e['name'] for e in plan['execution_order']}),960)
        self.assertEqual(plan['test_seeds'],[])
        self.assertFalse(plan['execution_ready'])
        self.assertEqual(sum(e['network_id']=='n5f2' for e in plan['execution_order']),192)

    def test_first_two_flows_keep_prior_training_timing(self):
        old=generate('stable',11)
        expected=[(p.hops[0].ingress_us,1 if p.flow.tag==1001 else 2) for p in old.packets]
        self.assertEqual(schedule(fork_chain(3,2),11),expected)
        self.assertEqual([r for r in schedule(fork_chain(5,8),11) if r[1]<=2],expected)

    def test_tasks_not_multiplied_by_carrier_count(self):
        self.assertEqual(demands(fork_chain(5,2),'shared_priority'),demands(fork_chain(5,8),'shared_priority'))

    def test_eight_nodes_keep_all_tasks_and_terminal_priority(self):
        ds=demands(fork_chain(8,2),'shared_priority')
        self.assertEqual([d['node'] for d in ds],list(range(1,9)))
        self.assertEqual(ds[-1]['target_us'],100000)
        self.assertEqual(ds[-1]['weight'],4)

    def test_scene_pairing_and_window_packet_denominator(self):
        a=inputs(fork_chain(5,8),'stable',29)
        b=inputs(fork_chain(5,8),'shared_priority',29)
        self.assertEqual(a[0],b[0]);self.assertEqual(len(a[0]),640)
        self.assertEqual(sum(2000000000<=p['offset_ns']<10000000000 for p in a[0]),512)
        self.assertEqual(len(a[2]),5)


if __name__=='__main__':unittest.main()
