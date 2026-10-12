"""全局选择、业务隔离、十方法分派与连续顺序的边界核对。"""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'experiments')]
from literature_stage_contract import specification, order, fields, validate
from literature_selection import select
from literature_matrix_case import planner_for
from check_literature_matrix import check_case
from orchestration.adaptive import AdaptiveSettings
from orchestration.control_ledger import ControlLedger
from rolling_workloads import MANAGEMENT, budget_for
from test_adaptive import view_at


def rows(stage='training', candidates=None):
    spec = specification()
    indices = {m: range(6) for m in spec['literature_methods']} if candidates is None else candidates
    return [dict(scenario=p['scenario'], budget_level=p['budget_level'], seed=seed, method=method,
                 preset_index=index, controller_loss_upper=1 + index * .1, api_bytes_per_s=242,
                 window_feasibility='within')
            for p in spec['profiles'] for seed in spec[stage]['seeds'] for method, values in indices.items() for index in values]


class LiteratureStageTests(unittest.TestCase):
    def test_training_preserves_all_six_global_presets_and_contiguous_positions(self):
        spec = specification(); entries = order('training', spec)
        self.assertEqual(len(entries), 96)
        self.assertEqual([e['position'] for e in entries], list(range(96)))
        self.assertEqual({e['seed'] for e in entries}, {11, 29})
        self.assertEqual(len({e['name'] for e in entries}), 96)
        for p in spec['profiles']:
            self.assertTrue(all(e['initial_config'] == p['initial_config'] for e in entries if e['scenario'] == p['scenario']))

    def test_budget_failures_take_priority_over_lower_loss(self):
        data = rows()
        for r in data:
            if r['preset_index'] == 0:
                r.update(controller_loss_upper=.01, window_feasibility='exceeds')
        selected = select(data, 'training', specification())
        self.assertEqual(selected['selected_indices'], {'intopp_due': [1, 2], 'd_into_tt': [1, 2]})

    def test_unavailable_retained_as_failure(self):
        data = rows(); data[0]['window_feasibility'] = 'unavailable'
        result = select(data, 'training', specification())
        score = next(r for r in result['scores'] if r['method'] == data[0]['method'] and r['preset_index'] == 0)
        self.assertEqual(score['score'][0], 1)

    def test_global_worst_profile_prevents_per_scene_selection(self):
        data = rows()
        for r in data:
            if r['preset_index'] == 0:
                r['controller_loss_upper'] = 2 if r['scenario'] == 'stable' else .1
        result = select(data, 'training', specification())
        self.assertEqual(result['selected_indices']['intopp_due'], [1, 2])

    def test_missing_duplicate_and_test_seed_rows_rejected(self):
        data = rows()
        wrong = copy.deepcopy(data); wrong[0]['seed'] = 3001
        for corrupted in (data[:-1], data + [data[0]], wrong):
            with self.assertRaises(ValueError): select(corrupted, 'training', specification())

    def test_nonfinite_selection_input_rejected(self):
        data = rows(); data[0]['controller_loss_upper'] = float('nan')
        with self.assertRaises(ValueError): select(data, 'training', specification())

    def test_calibration_requires_training_candidates_and_separate_businesses(self):
        chosen = select(rows(), 'training', specification())
        plan = fields('calibration', chosen)
        self.assertEqual(plan['expected_cases'], 32)
        self.assertEqual({e['seed'] for e in plan['execution_order']}, {1301, 1423})
        self.assertEqual([e['position'] for e in plan['execution_order']], list(range(32)))
        with self.assertRaises(ValueError): fields('calibration', None)

    def test_exact_tie_uses_preset_index(self):
        data = rows()
        for r in data: r['controller_loss_upper'] = 1
        self.assertEqual(select(data, 'training', specification())['selected_indices']['d_into_tt'], [0, 1])

    def test_ten_methods_obey_shared_legal_view_and_limits(self):
        training = select(rows(), 'training', specification())
        final = select(rows('calibration', training['selected_indices']), 'calibration', specification(), training['selected_indices'])
        entries = order('comparison', specification(), final['selected_indices'], [3009, 3010, 3011, 3012])
        view = view_at(3000000, times=(100000, 600000))
        for method in specification()['performance']['methods']:
            entry = next(e for e in entries if e['method'] == method)
            planner = planner_for(entry, MANAGEMENT, budget_for('stable', 'high'), AdaptiveSettings(**entry['adaptive']))
            result, planned = planner.decide(view, ControlLedger().snapshot(view.now_us * 1000))
            self.assertLessEqual(len(result['candidate_scores']), 48)
            self.assertLessEqual(len(planned), 6)

    def test_checker_dispatch_preserves_original_and_literature_objectives(self):
        for method in specification()['performance']['methods']:
            with patch('check_literature_matrix.load', return_value={'method': method}), \
                 patch('check_literature_matrix.literature_case', return_value={}) as literature, \
                 patch('check_literature_matrix.reference_case', return_value={}) as original:
                check_case(Path('.'), [])
                self.assertEqual(literature.call_count, int(method in ('intopp_due', 'd_into_tt')))
                self.assertEqual(original.call_count, int(method not in ('intopp_due', 'd_into_tt')))

    def test_two_comparison_blocks_have_exact_contiguous_320_identities(self):
        training = select(rows(), 'training', specification())
        final = select(rows('calibration', training['selected_indices']), 'calibration', specification(), training['selected_indices'])
        audit = dict(selected_fresh_seeds=[3009, 3010, 3011, 3012], excluded_seeds=list(range(3001, 3009)))
        plan = fields('comparison', final, audit); validate(plan)
        entries = plan['execution_order']
        self.assertEqual([e['position'] for e in entries], list(range(320)))
        self.assertEqual(len({e['name'] for e in entries}), 320)
        self.assertEqual([sum(e['block'] == b for e in entries) for b in (1, 2)], [160, 160])
        self.assertFalse(plan['fresh_inputs_generated'])

    def test_consumed_comparison_seeds_and_altered_initialization_rejected(self):
        plan = fields('training'); bad = copy.deepcopy(plan)
        bad['execution_order'][0]['initial_config'][0] = 50000
        with self.assertRaises(ValueError): validate(bad)
        training = select(rows(), 'training', specification())
        final = select(rows('calibration', training['selected_indices']), 'calibration', specification(), training['selected_indices'])
        with self.assertRaises(ValueError): fields('comparison', final, dict(selected_fresh_seeds=[3001, 3010, 3011, 3012], excluded_seeds=[]))


if __name__ == '__main__': unittest.main()
