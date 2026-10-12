"""有限训练的独立排序拒绝、六层支配及未知费用保留。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
from analyze_observer_training import age_bounds
from check_observer_training_analysis import check_integrals, check_selection
from observer_training_contract import CANDIDATE_PATH, select
from test_observer_training import fixture


class ObserverTrainingAnalysisTests(unittest.TestCase):
    def check(self, mutate=None, unknown=False):
        plan, values = fixture()
        if unknown:
            for r in values:
                if r['candidate_index'] == 1:
                    r['export_upper_bytes_per_s'] = None
                    r['window_feasibility'] = 'unavailable'
        scores, winners = select(plan, values)
        with tempfile.TemporaryDirectory() as name:
            output = Path(name)
            objects = {'training-plan.json': plan, 'candidate-plan.json': json.loads(CANDIDATE_PATH.read_text()),
                'candidate-scores.json': scores, 'training-selections.json': winners,
                'finite-frontier.json': [s for s in scores if s['finite_subset_nondominated']],
                'findings.json': {'selected_candidates': {r['scenario']: r['selected_candidate'] for r in winners}}}
            if mutate: mutate(objects)
            for filename, value in objects.items():
                (output / filename).write_text(json.dumps(value, ensure_ascii=False))
            table = [{k: '' if v is None else str(v) for k, v in r.items()} for r in values]
            return check_selection(output, table)

    def test_complete_independent_selection_and_frontier(self):
        self.assertGreater(self.check(), 1000)

    def test_wrong_winner_rejected(self):
        def mutate(objects): objects['training-selections.json'][0]['selected_candidate'] = 2
        with self.assertRaises(ValueError): self.check(mutate)

    def test_forged_six_layer_dominance_rejected(self):
        def mutate(objects): objects['candidate-scores.json'][3]['dominated_by'] = []
        with self.assertRaises(ValueError): self.check(mutate)

    def test_removed_infeasible_frontier_or_unknown_cost_rejected(self):
        def mutate(objects): objects['finite-frontier.json'].pop()
        with self.assertRaises(ValueError): self.check(mutate)
        self.assertGreater(self.check(unknown=True), 1000)

    def test_unknown_tasks_are_in_age_and_denominator(self):
        tasks = [{'object_id': {'node': 1, 'port': 2, 'queue': 0}, 'start_us': 2000000, 'end_us': 10000000,
                  'target_us': 100000, 'weight': 4}]
        bounds = age_bounds(tasks, [], 0)
        self.assertEqual([bounds['loss_lower'], bounds['loss_upper']], [4, 4])
        self.assertEqual(bounds['weighted_duration_us'], 32000000)
        check_integrals(tasks, [], 0, bounds)
