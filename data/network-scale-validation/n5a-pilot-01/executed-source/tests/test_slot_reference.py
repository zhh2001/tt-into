"""因果观察、成功状态连续性和收费口径的事件适配检查。"""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from experiments.run_assignment_reference import make_workload
from orchestration.path_reference import Policy, replay, violations
from orchestration.slot_reference import START, current_deadlines, run_slot_reference

RPC = {'installation_bytes': 8958, 'write_bytes': {'TTEgress.time_threshold': 124, 'TTEgress.max_count': 119}}


class SlotTests(unittest.TestCase):
    def execute(self, w, family='dinto', parameters=None):
        if parameters is None:
            parameters = {'gain': 'age', 'tradeoff': 100., 'average_change_bytes': 24.}
        return run_slot_reference(w['packets'], w['phases'], family, parameters, 1, RPC, w['budget'], detailed=True)

    def test_future_packet_suffix_cannot_change_earlier_actions(self):
        w = make_workload('jitter', 100)
        altered = dict(w, packets=[p for p in w['packets'] if p.gate_us[0] < 1000000])
        for family, parameters in (('dinto', None), ('intopp_ahead', {'lookahead_us': 100000})):
            before, after = self.execute(w, family, parameters), self.execute(altered, family, parameters)
            self.assertEqual([r for r in before['decisions'] if r['issued_us'] <= 1000000],
                             [r for r in after['decisions'] if r['issued_us'] <= 1000000])

    def test_unreceived_generation_is_not_visible(self):
        w = make_workload('jitter', 101)
        delayed = dict(w, packets=[replace(p, arrival_us=max(p.arrival_us, 1000000)) for p in w['packets']])
        result = self.execute(delayed)
        self.assertTrue(all(all(v is None for v in r['latest_received_generated_us'].values())
                            for r in result['decisions'] if r['issued_us'] < 1000000))

    def test_replay_retains_success_state_across_updates(self):
        w = make_workload('deadline_change', 100)
        params = {'branch1': 'margin', 'branch2': 'margin', 'shared': 'half'}
        result = self.execute(w, 'tt', params)
        schedule = [(r['effective_us'], Policy({(v['node'], v['flow']): v['threshold_us'] for v in r['thresholds']}))
                    for r in result['schedule']]
        replayed = replay(w['packets'], schedule)
        self.assertEqual(result['selected'], replayed['selected'])
        self.assertEqual(result['samples'], replayed['samples'])
        self.assertEqual(result['violation_fractions_by_phase'], violations(replayed['samples'], w['phases']))
        self.assertFalse(any('reset' in row['register'] for d in result['decisions'] for row in d['writes']))

    def test_api_cost_and_data_cost_are_separate(self):
        w = make_workload('jitter', 100)
        result = self.execute(w)
        self.assertEqual(result['runtime_api_update_serialized_bytes'], sum(d['update_serialized_bytes'] for d in result['decisions']))
        self.assertEqual(result['runtime_api_total_serialized_bytes'], 8958 + result['runtime_api_update_serialized_bytes'])
        self.assertEqual(result['data_total_byte_hops'], sum(result['inband_per_link_bytes']) + result['export_bytes'])

    def test_infeasible_budget_retains_all_packets_and_results(self):
        w = make_workload('jitter', 100)
        w['budget']['runtime_api_serialized_bytes'] = 8957
        result = self.execute(w)
        self.assertFalse(result['budget_satisfied'])
        self.assertEqual(result['budget_qualified_weight'], 0)
        self.assertEqual(len(result['selected']), len(w['packets']))
        self.assertGreater(result['runtime_api_total_serialized_bytes'], 8957)

    def test_future_deadline_is_hidden_until_notification(self):
        w = make_workload('deadline_change', 100)
        self.assertEqual(current_deadlines(1219999, w['phases']), (150000, 150000, 250000))
        self.assertEqual(current_deadlines(1220000, w['phases']), (650000, 650000, 175000))
        result = self.execute(w, 'intopp_ahead', {'lookahead_us': 100000})
        self.assertTrue(all(tuple(r['deadlines_us']) == (150000, 150000, 250000)
                            for r in result['decisions'] if r['issued_us'] < 1220000))

    def test_intrinsic_no_traffic_gap_survives_relaxed_capacity(self):
        w = make_workload('low_traffic', 100)
        self.assertNotIn(1, w['relaxed_opportunity_fulfilled_nodes'])


if __name__ == '__main__':
    unittest.main()
