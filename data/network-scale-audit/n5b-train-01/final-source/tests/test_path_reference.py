"""检查真实路径资格、替换费用、跨阶段前史及旧阈值执行边界。"""
import sys
from pathlib import Path
import unittest
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from orchestration.path_reference import Opportunity, Phase, Policy, evaluate, fork_packets, replay, static_policies, validate, violations


class PathTests(unittest.TestCase):
    def test_capacity_two_removes_dynamic_need_with_exact_padding_cost(self):
        packets = [replace(p, capacity=2) for p in fork_packets()]
        phases = [Phase(0, 1220000, (150000, 150000, 250000)), Phase(1220000, 2400000, (650000, 650000, 175000))]
        policy = Policy(dict.fromkeys([(1, 1), (2, 2), (3, 1), (3, 2), (3, 3)], 1))
        validate(packets, phases, [(-800000, policy)])
        result = replay(packets, [(-800000, policy)])
        self.assertEqual(violations(result['samples'], phases), [[0, 0, 0], [0, 0, 0]])
        self.assertEqual(result['inband_per_link_bytes'], [512, 512, 2048])
        self.assertEqual(result['export_bytes'], 4928)
        self.assertEqual(result['selected'][1], [3, 1])

    def test_exhaustion_covers_executable_indices_and_integer_weights(self):
        flow_policies = list(static_policies([1], 'flow'))
        self.assertEqual(len(flow_policies), 27)
        self.assertEqual(set(flow_policies[0].thresholds), {(1, 1), (2, 2), (3, 1), (3, 2), (3, 3)})
        weighted = list(static_policies([1], 'port', True))
        self.assertEqual(len(weighted), 19)
        self.assertIn((1, 1, 4), [p.weights for p in weighted])
        self.assertTrue(all(p.format_name == 'task' for p in weighted))

    def test_physical_eligibility_and_shared_object(self):
        packets = fork_packets(start_us=-200000, end_us=200000)
        policy = Policy({(1, 1): None, (2, 2): None, (3, 1): 1, (3, 2): 1, (3, 3): 1})
        result = replay(packets, [(-200000, policy)])
        self.assertEqual(result['successful_insertions'], [0, 0, len(packets)])
        self.assertEqual(result['inband_per_link_bytes'], [16, 16, 160])
        self.assertTrue(all(v == [3] for v in result['selected'].values()))
        for p in packets:
            self.assertTrue(set(result['selected'][p.packet_id]) <= set(p.path))

    def test_replacement_preserves_upstream_cost_and_gate(self):
        packets = [Opportunity(1, 1, (1, 3), (0, 2), (0, 1), (0, 1), 2),
                   Opportunity(2, 1, (1, 3), (0, 2), (10, 11), (10, 11), 12)]
        policy = Policy({(1, 1): 100, (3, 1): 1}, selection='reservoir', weights=(1, 1, 31), format_name='task')
        result = replay(packets, [(-1, policy)], detailed=True)
        self.assertEqual(result['selected'], {1: [3], 2: [3]})
        self.assertEqual(result['inband_per_link_bytes'], [22, 0, 34])
        self.assertEqual(result['successful_insertions'], [1, 0, 2])
        self.assertEqual(result['events'][2]['decision'], 'time')

    def test_phase_change_keeps_real_previous_success(self):
        packets = [Opportunity(i, 1, (1,), (0,), (t,), (t,), t + 1) for i, t in enumerate((0, 10, 20), 1)]
        policies = [(-1, Policy({(1, 1): 100})), (5, Policy({(1, 1): 20}))]
        result = replay(packets, policies)
        self.assertEqual(result['selected'], {1: [1], 2: [], 3: [1]})

    def test_counter_phase_and_mtu(self):
        packets = [Opportunity(i, 1, (1,), (0,), (i,), (i,), i + 1, ip_bytes=size)
                   for i, size in enumerate((1485, 120, 1497, 120), 1)]
        policy = Policy({(1, 1): 1}, ratios=(2, 1, 1), counter_offsets=(1, 0, 0))
        result = replay(packets, [(-1, policy)])
        self.assertEqual(result['selected'], {1: [], 2: [], 3: [], 4: []})
        self.assertEqual(result['inband_per_link_bytes'], [4, 0, 0])

    def test_loss_and_pre_export_drop_have_different_cost(self):
        policy = Policy({(1, 1): 1})
        lost = Opportunity(1, 1, (1,), (0,), (0,), (0,), None)
        dropped = Opportunity(1, 1, (1,), (0,), (0,), (0,), None, export_emitted=False)
        self.assertEqual(replay([lost], [(-1, policy)])['export_bytes'], 60)
        self.assertEqual(replay([dropped], [(-1, policy)])['export_bytes'], 0)

    def test_violation_integral_matches_independent_metrics(self):
        samples = {1: [(-5, -2), (6, 9), (3, 12), (25, 27)], 2: [], 3: [(0, 3), (19, 20)]}
        phases = [Phase(0, 15, (8, 12, 6)), Phase(15, 30, (10, 4, 8))]
        fast = violations(samples, phases)
        full = evaluate({'samples': samples}, phases)
        for i, phase in enumerate(full):
            for j, row in enumerate(phase['tasks']):
                self.assertAlmostEqual(fast[i][j], row['violation_fraction'])

    def test_predeclared_tt_schedule_and_failure_witness(self):
        packets = fork_packets()
        phases = [Phase(0, 1220000, (150000, 150000, 250000)), Phase(1220000, 2400000, (650000, 650000, 175000))]
        keys = [(1, 1), (2, 2), (3, 1), (3, 2), (3, 3)]
        early = Policy(dict.fromkeys(keys, 1))
        late = Policy({key: 300001 if key[0] < 3 else 1 for key in keys})
        schedule = [(-800000, early), (1200000, late)]
        validate(packets, phases, schedule)
        self.assertEqual(violations(replay(packets, schedule)['samples'], phases), [[0, 0, 0], [0, 0, 0]])
        static = violations(replay(packets, [(-800000, early)])['samples'], phases)
        self.assertGreater(static[1][2], 0)

    def test_rejects_noncausal_paths_and_unsupported_wire(self):
        phases = [Phase(0, 10, (2, 2, 2))]
        bad = Opportunity(1, 1, (1, 3), (0, 2), (0, 1), (2, 3), 4)
        with self.assertRaises(ValueError):
            validate([bad], phases, [(-1, Policy({(1, 1): 1, (3, 1): 1}))])
        good = Opportunity(1, 1, (1,), (0,), (0,), (0,), 1)
        with self.assertRaises(ValueError):
            validate([good], phases, [(-1, Policy({(1, 1): 1}, selection='reservoir'))])


if __name__ == '__main__':
    unittest.main()
