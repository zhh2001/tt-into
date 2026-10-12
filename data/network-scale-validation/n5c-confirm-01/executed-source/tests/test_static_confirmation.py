"""确认重复分母、完整预算资格、冻结排序及独立破坏核对。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments'))
from confirmation_contract import execution_order, select_confirmation, validate_plan
from check_confirmation_analysis import check_selection
from check_confirmation_analysis import check_repeats
from confirmation_repeats import summarize_repeats


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / 'data/network-static-plan/n3f-confirmation-01'
        self.plan = json.loads((self.directory / 'confirmation-plan.json').read_text())

    def summaries(self):
        return [dict(e, loss_lower=.9, loss_upper=1., window_feasibility='within') for e in execution_order(self.plan)]

    def test_order_has_twelve_cells_in_each_block(self):
        order = execution_order(self.plan)
        self.assertEqual(len({r['name'] for r in order}), 36)
        for block in (1, 2, 3):
            rs = [r for r in order if r['block'] == block]
            self.assertEqual(len(rs), 12)
            self.assertEqual({r['seed'] for r in rs}, {11, 29})
            self.assertEqual(len({(r['scenario'], r['candidate_index'], r['seed']) for r in rs}), 12)

    def test_changed_config_rejected(self):
        self.plan['unique_config_cells'][0]['config'][0] = 50000
        with self.assertRaises(ValueError): validate_plan(self.plan)

    def test_changed_order_rejected(self):
        self.plan['execution_order'].reverse()
        with self.assertRaises(ValueError): validate_plan(self.plan)

    def test_generation_source_damage_rejected(self):
        self.plan['source_sha256']['experiments/prepare_static_confirmation.py'] = '0' * 64
        with self.assertRaisesRegex(ValueError, '源码'): validate_plan(self.plan, self.directory)

    def test_missing_repeat_rejected(self):
        with self.assertRaisesRegex(ValueError, '分母'): select_confirmation(self.plan, self.summaries()[:-1])

    def test_duplicate_repeat_rejected(self):
        rs = self.summaries()
        rs[-1] = copy.deepcopy(rs[0])
        with self.assertRaisesRegex(ValueError, '分母'): select_confirmation(self.plan, rs)

    def test_one_budget_failure_excludes_all_six(self):
        for state in ('exceeds', 'ambiguous', 'unavailable'):
            rs = self.summaries()
            r = next(r for r in rs if r['scenario'] == 'shared_priority' and r['candidate_index'] == 2)
            r['window_feasibility'] = state
            scores, frozen = select_confirmation(self.plan, rs)
            self.assertFalse(next(r for r in scores if r['scenario'] == 'shared_priority' and r['candidate_index'] == 2)['feasible'])
            self.assertEqual(frozen[1]['selected_candidate'], 3)

    def test_worst_upper_precedes_mean(self):
        rs = self.summaries()
        for r in rs:
            if r['scenario'] == 'shared_priority' and r['candidate_index'] == 2:
                r['loss_lower'], r['loss_upper'] = 0., .1
        next(r for r in rs if r['scenario'] == 'shared_priority' and r['candidate_index'] == 2)['loss_upper'] = 1.1
        _, frozen = select_confirmation(self.plan, rs)
        self.assertEqual(frozen[1]['selected_candidate'], 3)

    def test_nan_age_excludes_candidate(self):
        rs = self.summaries()
        rs[0]['loss_upper'] = float('nan')
        scores, _ = select_confirmation(self.plan, rs)
        self.assertFalse(next(r for r in scores if r['scenario'] == rs[0]['scenario'] and r['candidate_index'] == rs[0]['candidate_index'])['age_evaluable'])

    def test_all_infeasible_keeps_four_cells(self):
        rs = self.summaries()
        for r in rs: r['window_feasibility'] = 'exceeds'
        _, frozen = select_confirmation(self.plan, rs)
        self.assertEqual(len(frozen), 4)
        self.assertTrue(all(r['status'] == 'finite_subset_infeasible' and r['selected_config'] is None for r in frozen))

    def fixture(self, d):
        rs = self.summaries()
        scores, frozen = select_confirmation(self.plan, rs)
        for file, value in (('confirmation-plan.json', self.plan), ('confirmation-scores.json', scores), ('frozen-static.json', frozen)):
            (d / file).write_text(json.dumps(value))
        return {r['name']: {k: str(v) for k, v in r.items()} for r in rs}

    def test_independent_selection_accepts_full_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.assertEqual(check_selection(d, self.fixture(d)), 28)

    def test_independent_selection_rejects_forged_winner(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            base = self.fixture(d)
            frozen = json.loads((d / 'frozen-static.json').read_text())
            frozen[1]['selected_candidate'] = 3
            (d / 'frozen-static.json').write_text(json.dumps(frozen))
            with self.assertRaises(ValueError): check_selection(d, base)

    def repeat_fixture(self, d):
        cases = execution_order(self.plan)
        (d / 'confirmation-plan.json').write_text(json.dumps(self.plan))
        for i, c in enumerate(cases):
            directory = d / c['name']
            directory.mkdir()
            packet = dict(packet_id=1000 + i, flow=1, offset_ns=1234, ip_length=64, path=[1, 3])
            report = dict(ether_type=0x88b6, packet_id=1000 + i, records=[dict(switch_id=1, hop=1)])
            (directory / 'packets.json').write_text(json.dumps([packet]))
            (directory / 'collector.jsonl').write_text(json.dumps(report) + '\n')
            (directory / 'tasks.json').write_text('[]')
            (directory / 'generation-bounds.json').write_text('{"loss_lower":0.9,"loss_upper":1.0}')
        summarize_repeats(d, cases)
        self.assertGreater(check_repeats(d, cases), 0)
        return cases

    def test_independent_repeats_rejects_forged_sample_ordinal(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            cases = self.repeat_fixture(d)
            positions = json.loads((d / 'sample-signatures.json').read_text())
            positions[cases[0]['name']][0][0] = 9
            (d / 'sample-signatures.json').write_text(json.dumps(positions))
            with self.assertRaises(ValueError): check_repeats(d, cases)

    def test_independent_repeats_rejects_wrong_candidate_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            cases = self.repeat_fixture(d)
            pairs = json.loads((d / 'candidate-repeat-pairs.json').read_text())
            pairs[0]['model_case'] = pairs[0]['alternative_case']
            (d / 'candidate-repeat-pairs.json').write_text(json.dumps(pairs))
            with self.assertRaises(ValueError): check_repeats(d, cases)

    def test_independent_repeats_rejects_missing_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            cases = self.repeat_fixture(d)
            groups = json.loads((d / 'seed-repeat-diagnostics.json').read_text())
            (d / 'seed-repeat-diagnostics.json').write_text(json.dumps(groups[:-1]))
            with self.assertRaises(ValueError): check_repeats(d, cases)


if __name__ == '__main__':
    unittest.main()
