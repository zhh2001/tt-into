"""手算反例与独立穷举检查分支接纳、连续期限和资源账本。"""
from dataclasses import replace
from itertools import combinations, product
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from orchestration.budget_model import Budget, ControlEvent, FORMATS
from orchestration.metrics import age_metrics
from orchestration.model import Task
from orchestration.path_reference import Opportunity, Phase, Policy, fork_packets, replay, validate
from orchestration.trace_admission import Demand, certificates, selection_metrics, solve


class AdmissionTests(unittest.TestCase):
    def test_individually_feasible_tasks_can_have_capacity_cut(self):
        p = Opportunity(1, 1, (1, 3), (0, 2), (-2, -1), (-2, -1), 0)
        tasks = [Demand(1, 2), Demand(3)]
        phases = [Phase(0, 5, (10, 10, 10))]
        for t in tasks:
            self.assertEqual(solve([t], [p], phases)['admitted_weight'], t.weight)
        proof = certificates(tasks, [p], phases)
        cut = proof['capacity_cuts'][0]
        self.assertEqual((cut['required_slots'], cut['available_slots']), (2, 1))
        self.assertEqual(cut['nodes'], [1, 3])
        self.assertEqual(solve(tasks, [p], phases)['accepted_nodes'], [1])
        self.assertEqual(solve(tasks, [p], phases, force_all=True)['status'], 'infeasible')
        self.assertEqual(solve(tasks, [replace(p, capacity=2)], phases)['admitted_weight'], 3)

    def test_delay_and_loss_are_not_opportunities_at_collector(self):
        p = Opportunity(1, 1, (1,), (0,), (4,), (4,), 9)
        phases = [Phase(0, 8, (5, 5, 5))]
        tasks = [Demand(1, initial_generated_us=0)]
        for packet in (p, replace(p, arrival_us=None)):
            proof = certificates(tasks, [packet], phases)
            self.assertTrue(any(r['kind'] == 'opportunity_gap' for r in proof['gaps']))
            self.assertEqual(solve(tasks, [packet], phases)['admitted_weight'], 0)
        self.assertEqual(solve(tasks, [replace(p, arrival_us=5)], phases)['admitted_weight'], 1)

    def test_end_arrival_cannot_repair_prior_positive_violation(self):
        tasks = [Demand(1, initial_generated_us=0)]
        phases = [Phase(0, 10, (5, 5, 5))]
        p = Opportunity(1, 1, (1,), (0,), (9,), (9,), 10)
        self.assertEqual(solve(tasks, [p], phases)['admitted_weight'], 0)
        p = Opportunity(1, 1, (1,), (0,), (4,), (4,), 5)
        phases = [Phase(0, 9, (5, 5, 5))]
        self.assertEqual(solve(tasks, [p], phases)['admitted_weight'], 1)

    def test_tighter_phase_preserves_received_history(self):
        tasks = [Demand(1, initial_generated_us=0)]
        phases = [Phase(0, 5, (10, 10, 10)), Phase(5, 8, (4, 4, 4))]
        p = Opportunity(1, 1, (1,), (0,), (4,), (4,), 5)
        self.assertEqual(solve(tasks, [p], phases)['admitted_weight'], 1)
        self.assertEqual(solve(tasks, [replace(p, arrival_us=6)], phases)['admitted_weight'], 0)

    def test_checks_are_necessary_not_sufficient_with_time_gate(self):
        tasks = [Demand(1, initial_generated_us=0)]
        packets = [Opportunity(i, 1, (1,), (0,), (t,), (t,), t) for i, t in enumerate((4, 8), 1)]
        phases = [Phase(0, 12, (5, 5, 5))]
        self.assertTrue(certificates(tasks, packets, phases)['necessary_checks_passed'])
        self.assertEqual(solve(tasks, packets, phases)['admitted_weight'], 1)
        self.assertEqual(solve(tasks, packets, phases, thresholds={(1, 1): 5})['admitted_weight'], 0)

    def test_flow_and_port_success_gates_have_different_constraints(self):
        tasks = [Demand(3, initial_generated_us=0)]
        packets = [Opportunity(1, 1, (3,), (2,), (4,), (4,), 4),
                   Opportunity(2, 2, (3,), (2,), (8,), (8,), 8)]
        phases = [Phase(0, 12, (5, 5, 5))]
        self.assertEqual(solve(tasks, packets, phases, thresholds={(3, 1): 5, (3, 2): 5})['admitted_weight'], 1)
        self.assertEqual(solve(tasks, packets, phases, gate_scope='port', thresholds={(3, 0): 5})['admitted_weight'], 0)
        self.assertEqual(solve(tasks, packets, phases, thresholds={(3, 1): 5}, initial_gate={(3, 1): 0})['admitted_weight'], 0)

    def test_mtu_gap_and_exact_branch_cost(self):
        p = Opportunity(1, 1, (1, 3), (0, 2), (-2, -1), (-2, -1), 0, ip_bytes=1485, capacity=2)
        tasks, phases = [Demand(3)], [Phase(0, 2, (5, 5, 5))]
        self.assertEqual(certificates(tasks, [p], phases)['gaps'][0]['kind'], 'mtu_gap')
        p = replace(p, ip_bytes=1484)
        result = solve(tasks, [p], phases)
        self.assertEqual(result['cost'], {'inband_per_link_bytes': [4, 0, 16],
                                          'export_bytes': 60, 'data_total_byte_hops': 80})

    def test_pre_window_envelopes_consume_budget(self):
        tasks = [Demand(1, initial_generated_us=0)]
        packets = [Opportunity(1, 1, (1,), (0,), (-10,), (-10,), -9)]
        phases = [Phase(0, 4, (5, 5, 5))]
        budget = Budget((0, 0, 0), 0)
        self.assertEqual(solve(tasks, packets, phases, budget)['status'], 'infeasible')
        optional = solve(tasks, packets, phases, budget, source_mode='optional')
        self.assertEqual(optional['admitted_weight'], 1)
        self.assertFalse(optional['deployment_feasibility_established'])

    def test_observed_history_cost_and_rejected_task_samples_are_retained(self):
        tasks = [Demand(1), Demand(3)]
        p = Opportunity(1, 1, (1, 3), (0, 2), (-2, -1), (-2, -1), 0)
        phases = [Phase(0, 5, (2, 10, 10))]
        result = solve(tasks, [p], phases, locked_history={1: [1]})
        self.assertEqual(result['accepted_nodes'], [])
        self.assertEqual(result['selected'], {1: [1]})
        self.assertEqual(result['cost']['inband_per_link_bytes'], [16, 0, 16])
        self.assertEqual(result['cost']['export_bytes'], 60)
        self.assertEqual(solve(tasks, [p], phases)['accepted_nodes'], [3])
        with self.assertRaises(ValueError):
            solve(tasks, [replace(p, gate_us=(1, 2), generated_us=(1, 2), arrival_us=3)],
                  phases, locked_history={1: [1]})

    def test_fixed_control_cost_can_make_any_admission_impossible(self):
        p = Opportunity(1, 1, (1,), (0,), (1,), (1,), 2)
        task, phases = [Demand(1, initial_generated_us=0)], [Phase(0, 4, (5, 5, 5))]
        event = ControlEvent('installation', -10, -1, (20,), 'declared fixture', 'serialized_rpc')
        result = solve(task, [p], phases, Budget((100, 100, 100), 100, (19,)), control_events=[event])
        self.assertEqual((result['status'], result['reason']), ('infeasible', 'fixed_control_budget'))
        result = solve(task, [p], phases, Budget((100, 100, 100), 100, (20,)), control_events=[event])
        self.assertEqual(result['control_cost']['control_basis'], 'serialized_rpc')
        self.assertEqual(result['cost']['data_total_byte_hops'], 4)

    def test_out_of_order_report_cannot_rewind_latest_generation(self):
        packets = [Opportunity(1, 1, (1,), (0,), (1,), (1,), 6),
                   Opportunity(2, 1, (1,), (0,), (4,), (4,), 5)]
        rows = selection_metrics([Demand(1, initial_generated_us=0)], packets,
                                 [Phase(0, 8, (5, 5, 5))], {1: [1], 2: [1]})
        self.assertEqual(rows[0]['tasks'][0]['violation_fraction'], 0)

    def test_invalid_or_noncausal_input_rejected(self):
        p = Opportunity(1, 1, (1,), (0,), (0,), (0,), 1)
        phases = [Phase(0, 2, (5, 5, 5))]
        for tasks in ([], [Demand(1, 0)], [Demand(1, initial_generated_us=1)]):
            with self.assertRaises(ValueError):
                solve(tasks, [p], phases)
        with self.assertRaises(ValueError):
            solve([Demand(1)], [], phases)
        with self.assertRaises(ValueError):
            solve([Demand(1)], [replace(p, arrival_us=-1)], phases)
        with self.assertRaises(ValueError):
            solve([Demand(1)], [p], phases, Budget((100,), 100))

    def test_deferred_permissions_keep_three_real_warmup_samples(self):
        keys = [(1, 1), (2, 2), (3, 1), (3, 2), (3, 3)]
        cold = dict.fromkeys(keys, None)
        shared = {**cold, (3, 3): 1}
        first = {**shared, (1, 1): 1}
        second = {**first, (2, 2): 1}
        schedule = [(-800000, Policy(cold)), (-175001, Policy(shared)),
                    (-100001, Policy(first)), (-50001, Policy(second)), (0, Policy(dict.fromkeys(keys, 1)))]
        packets = [p for p in fork_packets() if p.gate_us[0] < 0]
        phases = [Phase(0, 10, (150000, 150000, 250000))]
        validate(packets, phases, schedule)
        result = replay(packets, schedule)
        self.assertEqual(result['successful_insertions'], [1, 1, 1])
        self.assertEqual(result['inband_per_link_bytes'], [44, 44, 116])
        self.assertEqual(result['export_bytes'], 180)
        self.assertEqual(result['samples'][1][0][0], -99995)
        self.assertEqual(result['samples'][2][0][0], -49995)
        self.assertEqual(result['samples'][3][0][0], -174995)

    def test_small_random_forks_match_exhaustive_age_and_bytes(self):
        rng = random.Random(7021)
        for trial in range(8):
            tasks = [Demand(1, 2, -1), Demand(3, 1, -1)]
            phases = [Phase(0, 7, (rng.choice((4, 7)), 9, rng.choice((4, 7))))]
            packets = [Opportunity(i + 1, 1 if i % 2 == 0 else 3,
                                   (1, 3) if i % 2 == 0 else (3,),
                                   (0, 2) if i % 2 == 0 else (2,),
                                   (t, t + .1) if i % 2 == 0 else (t,),
                                   (t, t + .1) if i % 2 == 0 else (t,),
                                   None if trial == 3 and i == 1 else t + rng.choice((.2, 2)),
                                   capacity=rng.choice((1, 2))) for i, t in enumerate((1, 3, 5))]
            for fmt, mode in product((FORMATS['prefix'], FORMATS['common']), ('fixed', 'optional')):
                budget = Budget((rng.randrange(20, 90), 0, rng.randrange(30, 130)), rng.choice((60, 120, 240)))
                options = []
                for p in packets:
                    choices = [(True, subset) for length in range(min(p.capacity, len(p.path)) + 1)
                               for subset in combinations(p.path, length)]
                    if mode == 'optional':
                        choices += [(False, ())]
                    options.append(choices)
                best = None
                for choices in product(*options):
                    wire, export = [0, 0, 0], 0
                    samples = {t.node: [] for t in tasks}
                    for p, (active, chosen) in zip(packets, choices):
                        for i, edge in enumerate(p.links):
                            wire[edge] += active * fmt.header_bytes + fmt.record_bytes * sum(n in chosen for n in p.path[:i + 1])
                        if chosen:
                            export += max(60, 34 + fmt.header_bytes + fmt.record_bytes * len(chosen))
                        if p.arrival_us is not None:
                            for n in chosen:
                                samples[n].append((p.generated_us[p.path.index(n)], p.arrival_us))
                    if any(v > b for v, b in zip(wire, budget.inband_per_link_bytes)) or export > budget.export_bytes:
                        continue
                    weight = sum(t.weight for t in tasks if age_metrics(
                        Task(t.node, phases[0].deadlines_us[t.node - 1], initial_generated_us=-1),
                        samples[t.node], 0, 7)['violation_fraction'] <= 1e-10)
                    score = (-weight, sum(wire) + export)
                    best = score if best is None or score < best else best
                result = solve(tasks, packets, phases, budget, fmt, source_mode=mode)
                if best is None:
                    self.assertEqual(result['status'], 'infeasible')
                else:
                    self.assertEqual((-result['admitted_weight'], result['cost']['data_total_byte_hops']), best,
                                     (trial, fmt.name, mode))


if __name__ == '__main__':
    unittest.main()
