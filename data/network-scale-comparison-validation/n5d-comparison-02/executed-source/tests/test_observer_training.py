"""完整训练分母、控制器主目标、失败保留和六层前沿的意义检查。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
from observer_training_contract import (ROOT, CANDIDATE_PATH, CANDIDATE_SHA256, METHOD_SHA256,
    ORDER_SEED, LAYERS, METHODS, expected_order, protocol, select, validate)
from network_comparison_contract import audit_consumption


def fixture():
    entries = expected_order(json.loads(CANDIDATE_PATH.read_text()))
    plan = {'status': 'prepared', 'stage': 'controller_observer_static_training', 'independent_test': False,
        'training_seeds': [11, 29], 'expected_cases': 192, 'repeats_per_candidate_business': 1,
        'candidate_plan_sha256': CANDIDATE_SHA256, 'method_contract_sha256': METHOD_SHA256,
        'primary_observer': 'controller', 'observation_period_us': 100000, 'decision_period_us': 1000000,
        'duration_us': 10000000, 'order_seed': ORDER_SEED, 'protocol': protocol(), 'execution_order': entries,
        'source_sha256': {}}
    summaries = []
    for e in entries:
        i = e['candidate_index']; upper = 4 if i == 0 else .5 if i == 1 else 1 + i / 100
        summaries.append(dict(e, case=e['name'], controller_loss_lower=upper - .01,
            controller_loss_upper=upper, collector_loss_lower=.1, collector_loss_upper=.2, window_feasibility='within',
            **{k + '_upper_bytes_per_s': 10 + i for k in LAYERS[:4]}, api_bytes_per_s=0, local_notice_bytes_per_s=0))
    return plan, summaries


class ObserverTrainingTests(unittest.TestCase):
    def test_protocol_has_eight_methods_and_pending_test(self):
        p = protocol()
        self.assertEqual(p['methods'], list(METHODS))
        self.assertEqual(p['primary_observer'], 'controller')
        self.assertEqual(p['test_expected_cases'], 256)
        self.assertTrue(p['parameter_selection_pending'])
        self.assertIsNone(p['fresh_business_seeds'])
        self.assertFalse(p['fresh_inputs_generated'])

    def test_full_candidates_seeds_and_order(self):
        plan, _ = fixture(); validate(plan)
        entries = plan['execution_order']
        self.assertEqual(len(entries), 192)
        self.assertEqual({e['seed'] for e in entries}, {11, 29})
        self.assertEqual(entries, expected_order(json.loads(CANDIDATE_PATH.read_text())))
        self.assertEqual(len({e['name'] for e in entries}), 192)

    def test_changed_metric_period_budget_or_methods_rejected(self):
        for mutate in (lambda p: p.update(primary_observer='collector'), lambda p: p.update(observation_period_us=50000),
                       lambda p: p['protocol']['budget']['low'].update(export=600), lambda p: p['protocol']['methods'].pop(),
                       lambda p: p.update(training_seeds=[11, 3005])):
            plan, _ = fixture(); mutate(plan)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): validate(plan)

    def test_incomplete_execution_source_set_rejected(self):
        plan, _ = fixture()
        with self.assertRaisesRegex(ValueError, '集合'): validate(plan, current=True)

    def test_selected_from_all_candidates_and_not_collector(self):
        plan, values = fixture()
        for r in values:
            if r['candidate_index'] == 2: r['collector_loss_upper'] = .001
        scores, frozen = select(plan, values)
        self.assertEqual(len(scores), 96)
        self.assertEqual([r['selected_candidate'] for r in frozen], [1] * 4)
        self.assertTrue(all(r['status'] == 'training_selected_pending_confirmation' for r in frozen))

    def test_minimax_precedes_average(self):
        plan, values = fixture()
        for r in values:
            if r['candidate_index'] == 1:
                r['controller_loss_upper'] = .2 if r['seed'] == 11 else 1.4
                r['controller_loss_lower'] = r['controller_loss_upper'] - .01
            if r['candidate_index'] == 2: r.update(controller_loss_upper=.81, controller_loss_lower=.8)
        self.assertEqual([r['selected_candidate'] for r in select(plan, values)[1]], [2]*4)

    def test_one_budget_failure_disqualifies_candidate(self):
        plan, values = fixture()
        for r in values:
            if r['candidate_index'] == 1 and r['seed'] == 29: r['window_feasibility'] = 'exceeds'
        scores, frozen = select(plan, values)
        self.assertFalse(any(r['feasible'] for r in scores if r['candidate_index'] == 1))
        self.assertEqual([r['selected_candidate'] for r in frozen], [2]*4)

    def test_ambiguous_and_unavailable_not_within(self):
        for status in ('ambiguous', 'unavailable'):
            plan, values = fixture()
            for r in values:
                if r['candidate_index'] == 1: r['window_feasibility'] = status
            with self.subTest(status=status): self.assertEqual(select(plan, values)[1][0]['selected_candidate'], 2)

    def test_none_feasible_retains_failures(self):
        plan, values = fixture()
        for r in values: r['window_feasibility'] = 'exceeds'
        scores, frozen = select(plan, values)
        self.assertEqual(len(scores), 96)
        self.assertTrue(all(r['selected_config'] is None and r['status'] == 'finite_subset_infeasible' for r in frozen))

    def test_missing_duplicate_or_wrong_seed_rejected(self):
        for change in ('missing', 'duplicate', 'wrong_seed'):
            plan, values = fixture()
            if change == 'missing': values.pop()
            elif change == 'duplicate': values[-1] = copy.deepcopy(values[0])
            else: values[0]['seed'] = 3005
            with self.subTest(change=change), self.assertRaises(ValueError): select(plan, values)

    def test_nonfinite_age_or_unknown_cost_not_selected(self):
        for field, value in (('controller_loss_upper', float('nan')), ('export_upper_bytes_per_s', None)):
            plan, values = fixture()
            for r in values:
                if r['candidate_index'] == 1: r[field] = value
            with self.subTest(field=field): self.assertEqual(select(plan, values)[1][0]['selected_candidate'], 2)

    def test_frontier_uses_all_cost_layers_and_preserves_infeasible(self):
        plan, values = fixture()
        for r in values:
            if r['candidate_index'] == 2:
                r['e1_upper_bytes_per_s'] = 9
                r['window_feasibility'] = 'exceeds'
        scores, _ = select(plan, values)
        c2 = next(r for r in scores if r['scenario'] == 'stable' and r['candidate_index'] == 2)
        self.assertTrue(c2['finite_subset_nondominated'])
        self.assertFalse(c2['feasible'])
        c3 = next(r for r in scores if r['scenario'] == 'stable' and r['candidate_index'] == 3)
        self.assertIn(1, c3['dominated_by'])
        self.assertEqual(len(c3['frontier_vector']), 7)

    def test_consumed_and_reserved_seeds_separated(self):
        with tempfile.TemporaryDirectory() as name:
            data = Path(name) / 'data'; data.mkdir()
            (data / 'manifest.json').write_text(json.dumps({'status': 'passed', 'seeds': [3001, 3002, 3003, 3004]}))
            folder = data / 'prepared'; folder.mkdir()
            (folder / 'manifest.json').write_text(json.dumps({'status': 'prepared', 'fresh_business_seeds': [3005, 3006]}))
            audit = audit_consumption(data)
            self.assertEqual(audit['selected_fresh_seeds'], [3005, 3006, 3007, 3008])
            self.assertNotIn(3005, audit['consumed_seeds'])
            self.assertIn(3005, audit['reserved_seeds'])
            (folder / 'frozen-inputs.json').write_text(json.dumps({'generated': {'seed': 3005}}))
            self.assertNotIn(3005, audit_consumption(data)['selected_fresh_seeds'])

    def test_validate_only_generates_no_inputs_or_network(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            p = subprocess.run([sys.executable, str(ROOT / 'experiments/prepare_observer_training.py'), '--output', str(folder / 'plan')],
                               capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(p.returncode, 0, p.stderr)
            p = subprocess.run([sys.executable, str(ROOT / 'experiments/run_observer_training.py'),
                '--plan', str(folder / 'plan/training-plan.json'), '--output', str(folder / 'run'), '--validate-only'],
                capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertFalse(folder.joinpath('run').exists())
            result = json.loads(p.stdout)
            self.assertEqual(result['cases'], 192)
            self.assertFalse(result['inputs_generated'])
            self.assertFalse(result['network_started'])
