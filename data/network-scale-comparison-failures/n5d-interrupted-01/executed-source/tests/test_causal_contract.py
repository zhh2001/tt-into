"""因果接纳、连续期限边界与控制提前量的独立反例。"""
import unittest

from src.orchestration.causal_contract import (
    Opportunity, age_certificate, deployment_lead_certificate,
    two_opportunity_frontier,
)
from src.orchestration.metrics import age_metrics
from src.orchestration.model import Task


class CausalContractTests(unittest.TestCase):
    def frontier(self, **changes):
        inputs = dict(tasks=(1, 2), initial={1: -100000, 2: -100000},
                      deadlines={1: 200000, 2: 200000},
                      first=Opportunity(0, (1, 2)),
                      futures=(Opportunity(100000, (1,)), Opportunity(100000, (2,))),
                      capacity=1, record_budget=2, report_delay_us=0,
                      start_us=0, end_us=200000)
        inputs.update(changes)
        return two_opportunity_frontier(**inputs)

    def test_indistinguishable_prefix(self):
        row = self.frontier()
        self.assertEqual(row['offline_min_admitted'], 2)
        self.assertEqual(row['causal_admitted'], 1)
        self.assertEqual(row['offline'][0]['plan']['first'], [2])
        self.assertEqual(row['offline'][1]['plan']['first'], [1])
        self.assertEqual(row['causal']['plans'][0]['first'],
                         row['causal']['plans'][1]['first'])

    def test_extra_capacity_and_hint_close_gap(self):
        for args in [dict(capacity=2), dict(branch_hint=True)]:
            row = self.frontier(**args)
            self.assertEqual(row['causal_admitted'], 2)
            self.assertTrue(all(p['records'] <= 2 for p in row['causal']['plans']))

    def test_hint_does_not_create_supply(self):
        row = self.frontier(report_delay_us=60000, branch_hint=True)
        self.assertEqual(row['offline_min_admitted'], 1)
        self.assertEqual(row['causal_admitted'], 1)

    def test_record_budget_not_capacity_alone(self):
        self.assertEqual(self.frontier(capacity=2, record_budget=1)['causal_admitted'], 1)

    def test_hint_cannot_replace_previously_committed_task(self):
        row = self.frontier(deadlines={1: 150000, 2: 150000}, branch_hint=True)
        self.assertEqual([r['admitted'] for r in row['offline']], [[1], [2]])
        self.assertEqual(row['causal_admitted'], 0)

    def test_initial_history_may_need_no_packets(self):
        row = self.frontier(deadlines={1: 300000, 2: 300000}, record_budget=0)
        self.assertEqual(row['causal_admitted'], 2)
        self.assertTrue(all(p['records'] == 0 for p in row['causal']['plans']))

    def test_unknown_and_no_opportunity(self):
        self.assertEqual(age_certificate(100000, None, [], 0, 200000)['unknown_us'], 200000)
        self.assertEqual(age_certificate(100000, 0, [], 0, 200000)['violation_us'], 100000)

    def test_boundary_arrival_and_old_report(self):
        exact = age_certificate(100000, 0, [(100000, 100000)], 0, 200000)
        self.assertTrue(exact['fulfilled'])
        late = age_certificate(100000, 0, [(100000, 100001)], 0, 200000)
        self.assertEqual(late['violation_us'], 1)
        old = age_certificate(100000, 0, [(100000, 100000), (50000, 150000)], 0, 200000)
        self.assertEqual(old, exact)

    def test_integral_matches_existing_implementation(self):
        for initial in [None, -40000, 0]:
            for samples in [[], [(0, 20000)], [(100000, 120000), (50000, 150000)],
                            [(190000, 200000)]]:
                row = age_certificate(100000, initial, samples, 0, 200000)
                independent = age_metrics(Task(1, 100000, initial_generated_us=initial),
                                          samples, 0, 200000)
                self.assertAlmostEqual(row['violation_us'] / 200000,
                                       independent['violation_fraction'])
                self.assertAlmostEqual(row['unknown_us'] / 200000,
                                       independent['unknown_fraction'])

    def test_notice_lead_repairs_only_with_actual_opportunity(self):
        args = dict(packet_times=list(range(100000, 300000, 20000)),
                    install_delay_us=60000, report_delay_us=20000,
                    initial_generated_us=0, deadline_us=150000,
                    change_us=100000, end_us=300000)
        late = deployment_lead_certificate(notice_us=100000, **args)
        repaired = deployment_lead_certificate(notice_us=60000, **args)
        self.assertEqual(late['unavoidable_pre_report_violation_us'], 30000)
        self.assertEqual(late['age']['violation_us'], 30000)
        self.assertEqual(repaired['age']['violation_us'], 0)
        self.assertGreater(repaired['selected_records'], late['selected_records'])
        no_supply = deployment_lead_certificate(notice_us=0, **dict(args, packet_times=[]))
        self.assertEqual(no_supply['unavoidable_pre_report_violation_us'], 150000)

    def test_input_validation(self):
        for change in [dict(record_budget=-1), dict(report_delay_us=0.5),
                       dict(first=Opportunity(0, (1, 1))), dict(branch_hint=1),
                       dict(initial={1: 1, 2: 0}), dict(deadlines={1: 10}),
                       dict(futures=(Opportunity(0, (1,)),))]:
            with self.assertRaises(ValueError):
                self.frontier(**change)
        with self.assertRaises(ValueError):
            age_certificate(100, 0, [(20, 10)], 0, 100)


if __name__ == '__main__':
    unittest.main()
