"""破坏来源、目标和实际确认结算时独立检查必须拒绝。"""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'experiments')]
from control.literature_live import LiteraturePlanner
from orchestration.adaptive import AdaptiveSettings
from orchestration.control_ledger import ControlLedger
from orchestration.literature import LiteratureSettings
from run_rolling_reference import plain
from rolling_workloads import MANAGEMENT, budget_for
from check_literature_decisions import check_decisions
from literature_contract import method_contract, order
from test_adaptive import view_at


def fixture(method='d_into_tt'):
    v = view_at(3000000, times=(100000, 250000))
    planner = LiteraturePlanner(method, MANAGEMENT, budget_for('stable', 'high'), AdaptiveSettings(), (50000,) * 4)
    decision, _ = planner.decide(v, ControlLedger().snapshot(v.now_us * 1000))
    decision.update(view=v, time_us=v.now_us)
    return plain([decision]), {'method': method}, plain(dict(literature=LiteratureSettings(), settings=planner.options))


class LiteratureCheckTests(unittest.TestCase):
    def test_both_objectives_match_independent_checker(self):
        for method in ('intopp_due', 'd_into_tt'):
            args = fixture(method)
            self.assertGreater(check_decisions(*args), 100)

    def test_source_objective_capacity_and_state_corruption_rejected(self):
        base = fixture()
        def objective(d): d['candidate_scores'][0]['literature_objective'] += 1
        def capacity(d): d['candidate_scores'][0]['predicted_packet_choices'][0]['nodes'] = [1, 3]
        def queue(d): d['literature_state']['settled_change_bytes'] += 12
        def rate(d): d['rates'][0]['rate_pps'] += 1
        def recency(d): d['candidate_scores'][0]['literature_inputs'][0]['recency_us'] = 0
        for mutation in (objective, capacity, queue, rate, recency):
            with self.subTest(name=mutation.__name__), self.assertRaises(ValueError):
                args = copy.deepcopy(base)
                mutation(args[0][0])
                check_decisions(*args)

    def test_consumed_seed_only_and_contiguous_position(self):
        entries = order(method_contract())
        self.assertEqual([e['position'] for e in entries], list(range(8)))
        self.assertEqual({e['seed'] for e in entries}, {11})
        self.assertEqual(len({e['name'] for e in entries}), 8)
        self.assertEqual({e['method'] for e in entries}, {'intopp_due', 'd_into_tt'})


if __name__ == '__main__': unittest.main()
