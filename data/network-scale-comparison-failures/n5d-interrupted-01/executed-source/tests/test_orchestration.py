"""用可手算积分和穷举核对优化模型的边界及最优接纳。"""
import itertools
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from orchestration.model import Task, Packet, costs
from orchestration.metrics import age_metrics, evaluate
from orchestration.oracle import solve
from orchestration.replay import replay


class AgeTests(unittest.TestCase):
    def test_linear_area_and_violation(self):
        result = age_metrics(Task(1, 5, initial_generated_us=0), [], 0, 10)
        self.assertEqual(result['mean_age_us'], 5)
        self.assertEqual(result['violation_fraction'], 0.5)
        self.assertAlmostEqual(result['p95_age_us'], 9.5)

    def test_unknown_and_out_of_order(self):
        result = age_metrics(Task(1, 20), [(4, 5), (1, 7)], 0, 10)
        self.assertEqual(result['unknown_fraction'], 0.5)
        self.assertEqual(result['mean_known_age_us'], 3.5)
        self.assertIsNone(result['p95_age_us'])
        self.assertEqual(result['longest_no_effective_update_us'], 5)

    def test_arrival_and_deadline_boundary(self):
        result = age_metrics(Task(1, 5, initial_generated_us=-1), [(4, 4), (8, 10)], 0, 10)
        self.assertAlmostEqual(result['violation_fraction'], 0.1)


class ExecutionTests(unittest.TestCase):
    def test_capacity_drop_does_not_reset_gate(self):
        tasks = [Task(1, 100), Task(2, 100)]
        packets = [Packet(i, 1, (1, 2), (t, t + 1), (t, t + 1), t + 2) for i, t in enumerate([0, 5, 10], 1)]
        result = replay(tasks, packets, 10, 'tt_prefix')
        self.assertEqual(result['selected'], {1: [1], 2: [2], 3: [1]})

    def test_replacement_preserves_insertion_history(self):
        tasks = [Task(1, 100), Task(2, 100)]
        packets = [Packet(i, 1, (1, 2), (t, t + 1), (t, t + 1), t + 2) for i, t in enumerate([0, 1], 1)]
        result = replay(tasks, packets, 10, 'uniform_reservoir', seed=1)
        self.assertTrue(result['events'][0]['inserted'])
        self.assertFalse(next(event['inserted'] for event in result['events'] if event['packet_id'] == 2 and event['node'] == 1))
        self.assertGreaterEqual(result['cost']['inband_per_link_bytes'][0], 2 * 8 + 20)

    def test_mtu_and_empty_header_charges(self):
        p = Packet(1, 1, (1,), (0,), (0,), 1, ip_bytes=1492)
        self.assertEqual(p.slots(), 0)
        self.assertEqual(costs([p], {})['inband_per_link_bytes'], [8])
        self.assertEqual(costs([p], {})['export_bytes'], 0)

    def test_shared_gate_with_node_thresholds_can_fulfill_all(self):
        tasks = [Task(1, 250000, 3, initial_generated_us=-1000), Task(2, 450000, 2, initial_generated_us=-1000), Task(3, 650000, 1, initial_generated_us=-1000)]
        packets = []
        for t in range(0, 1200000, 200000):
            for flow in [1, 2]:
                packets.append(Packet(len(packets) + 1, flow, (1, 2, 3), (t, t + 100, t + 200), (t + 50, t + 150, t + 250), t + 1000))
        result = replay(tasks, packets, {1: 200000, 2: 400000, 3: 1}, 'shared_prefix')
        self.assertEqual(evaluate(tasks, packets, result['selected'], 0, 1200000)['fulfilled_task_count'], 3)


class OracleTests(unittest.TestCase):
    def test_gate_uses_ingress_not_generation(self):
        # 生成间隔 20，真实门控时间差仅 5，不能由生成时间放宽门控。
        tasks = [Task(1, 22, initial_generated_us=-3)]
        packets = [Packet(1, 1, (1,), (0,), (0,), 1), Packet(2, 1, (1,), (5,), (20,), 21)]
        answer = solve(tasks, packets, 10, 0, 40)
        self.assertEqual(answer['accepted_nodes'], [])
        self.assertEqual(solve(tasks, packets, 0, 0, 40)['accepted_nodes'], [1])

    def test_delay_can_make_deadline_impossible(self):
        tasks = [Task(1, 5, initial_generated_us=0)]
        packets = [Packet(1, 1, (1,), (4,), (4,), 10)]
        self.assertEqual(solve(tasks, packets, 0, 0, 15)['accepted_nodes'], [])

    def test_cold_start_cannot_claim_hard_feasibility(self):
        tasks = [Task(1, 10)]
        packets = [Packet(1, 1, (1,), (0,), (0,), 1)]
        self.assertEqual(solve(tasks, packets, 0, 0, 5)['accepted_nodes'], [])

    def test_exhaustive_small_schedule(self):
        tasks = [Task(1, 8, 2, initial_generated_us=-1), Task(2, 12, 1, initial_generated_us=-1)]
        packets = [Packet(i + 1, i % 2 + 1, (1, 2), (t, t + 0.1), (t, t + 0.1), t + 0.2) for i, t in enumerate([2, 6, 10, 14])]
        budgets = [100, 120, 248]
        best = 0
        for choices in itertools.product([0, 1, 2], repeat=4):
            chosen = {p.packet_id: [node] if node else [] for p, node in zip(packets, choices)}
            cost = costs(packets, chosen)
            if any(a > b for a, b in zip(cost['inband_per_link_bytes'] + [cost['export_bytes']], budgets)):
                continue
            measured = evaluate(tasks, packets, chosen, 0, 18)
            best = max(best, sum(task.weight for task, row in zip(tasks, measured['tasks']) if row['fulfilled']))
        result = solve(tasks, packets, 0, 0, 18, budgets)
        self.assertEqual(result['admitted_weight'], best)
        self.assertTrue(result['cost_optimal'])

    def test_fixed_header_can_exceed_budget(self):
        answer = solve([Task(1, 10, initial_generated_us=0)], [Packet(1, 1, (1,), (1,), (1,), 2)], 0, 0, 5, [7, 100])
        self.assertEqual(answer['status'], 'infeasible')


if __name__ == '__main__':
    unittest.main()
