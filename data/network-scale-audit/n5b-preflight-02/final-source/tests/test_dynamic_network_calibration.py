"""完整动态参数矩阵、费用可评价、全局排序与来源破坏边界。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments'))
from dynamic_calibration_contract import expected_order, validate_plan, select_parameters, normalized_excess, upper_rates
from check_dynamic_calibration_analysis import check_selection


class DynamicCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / 'data/network-dynamic-plan/n3g-calibration-01'
        self.plan = json.loads((self.directory / 'calibration-plan.json').read_text())

    def summaries(self):
        return [dict(case=e['name'], **{k: e[k] for k in ('scenario', 'budget_level', 'seed', 'method', 'parameter_index')},
                     loss_lower=.9, loss_upper=1., normalized_excess_upper=0., window_feasibility='within', successful_updates=1)
                for e in expected_order(self.plan)]

    def test_full_mixed_matrix(self):
        order = expected_order(self.plan)
        self.assertEqual(len({e['name'] for e in order}), 184)
        self.assertEqual(len([e for e in order if e['method'] == 'static']), 8)
        for method in ('uniform_guard', 'phase_guard', 'phase_local_guard'):
            for index in range(6):
                rs = [e for e in order if (e['method'], e['parameter_index']) == (method, index)]
                self.assertEqual(len(rs), 8)
                self.assertEqual({e['seed'] for e in rs}, {1301, 1423})

    def test_changed_preset_rejected(self):
        self.plan['execution_order'][0]['adaptive']['gain_margin'] = .4
        with self.assertRaises(ValueError): validate_plan(self.plan)

    def test_changed_budget_rejected(self):
        self.plan['profiles'][0]['budget']['export_bytes_per_s'] += 1
        with self.assertRaises(ValueError): validate_plan(self.plan)

    def test_removed_setting_rejected(self):
        self.plan['execution_order'].pop()
        with self.assertRaises(ValueError): validate_plan(self.plan)

    def test_source_damage_rejected(self):
        self.plan['source_sha256']['src/control/matched_live.py'] = '0' * 64
        with self.assertRaisesRegex(ValueError, '来源'): validate_plan(self.plan, self.directory)

    def test_source_and_initialization_binding(self):
        validate_plan(self.plan, self.directory, ROOT / 'data/network-static-validation/n3f-confirmation-02')

    def test_missing_case_rejected(self):
        with self.assertRaisesRegex(ValueError, '分母'): select_parameters(self.plan, self.summaries()[:-1])

    def test_duplicate_business_rejected(self):
        rs = self.summaries()
        rs[-1] = copy.deepcopy(rs[0])
        with self.assertRaisesRegex(ValueError, '分母'): select_parameters(self.plan, rs)

    def test_budget_failure_precedes_loss(self):
        rs = self.summaries()
        for r in rs:
            if (r['method'], r['parameter_index']) == ('uniform_guard', 0): r['loss_lower'], r['loss_upper'] = 0., .01
        next(r for r in rs if (r['method'], r['parameter_index']) == ('uniform_guard', 0))['window_feasibility'] = 'exceeds'
        _, frozen = select_parameters(self.plan, rs)
        self.assertEqual(frozen[0]['selected_parameter'], 1)

    def test_excess_precedes_loss_and_failed_freeze_is_explicit(self):
        rs = self.summaries()
        for r in rs:
            if r['method'] == 'uniform_guard':
                r['window_feasibility'], r['normalized_excess_upper'] = 'exceeds', .2 if r['parameter_index'] else .3
                if r['parameter_index'] == 0: r['loss_lower'], r['loss_upper'] = 0., .01
        _, frozen = select_parameters(self.plan, rs)
        self.assertEqual(frozen[0]['selected_parameter'], 1)
        self.assertEqual(frozen[0]['status'], 'frozen_with_budget_failures')
        self.assertEqual(frozen[0]['feasible_parameters'], 0)

    def test_unavailable_cost_never_counts_as_zero_excess(self):
        self.assertIsNone(upper_rates({'measurement_upper_bytes': None}))
        self.assertIsNone(normalized_excess(None, 'stable', 'high'))
        rs = self.summaries()
        for r in rs:
            if r['method'] == 'uniform_guard': r['normalized_excess_upper'] = None
        _, frozen = select_parameters(self.plan, rs)
        self.assertEqual(frozen[0]['status'], 'unavailable')

    def test_normalized_cost_uses_all_six_separate_layers(self):
        rates = dict(e1=240, e2=240, e3=780, export=900, api_serialized=2400, local_notice_payload=256)
        self.assertEqual(normalized_excess(rates, 'stable', 'high'), 3)

    def fixture(self, d):
        rs = self.summaries()
        scores, frozen = select_parameters(self.plan, rs)
        choices = {r['method']: r['selected_parameter'] for r in frozen}
        chosen = [r for r in rs if r['parameter_index'] is None or r['parameter_index'] == choices.get(r['method'], -1)]
        for file, value in (('calibration-plan.json', self.plan), ('parameter-scores.json', scores),
                            ('frozen-parameters.json', frozen), ('selected-calibration-cases.json', chosen)):
            (d / file).write_text(json.dumps(value))
        return {r['case']: {k: '' if v is None else str(v) for k, v in r.items()} for r in rs}

    def test_independent_checker_accepts_global_complete_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.assertGreater(check_selection(d, self.fixture(d)), 0)

    def test_independent_checker_rejects_forged_selected_parameter(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp); base = self.fixture(d)
            frozen = json.loads((d / 'frozen-parameters.json').read_text())
            frozen[0]['selected_parameter'] = 1
            (d / 'frozen-parameters.json').write_text(json.dumps(frozen))
            with self.assertRaises(ValueError): check_selection(d, base)

    def test_independent_checker_rejects_removed_fixed_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp); base = self.fixture(d)
            selected = json.loads((d / 'selected-calibration-cases.json').read_text())
            (d / 'selected-calibration-cases.json').write_text(json.dumps([r for r in selected if r['method'] != 'static']))
            with self.assertRaises(ValueError): check_selection(d, base)

    def test_runner_validates_inputs_without_root_or_network(self):
        r = subprocess.run([sys.executable, 'experiments/run_dynamic_network_calibration.py', '--validate-only',
                            '--output', '/tmp/unused-network-calibration-validation'], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads(r.stdout)
        self.assertEqual((result['status'], result['cases']), ('validated', 184))


if __name__ == '__main__':
    unittest.main()
