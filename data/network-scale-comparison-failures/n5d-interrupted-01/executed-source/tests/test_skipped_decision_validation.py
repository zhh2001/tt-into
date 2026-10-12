"""真实中断案例及整周期跳过的来源、时序和动作约束。"""
import copy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
from check_observation_wire import skipped_errors
from check_observation_reference import check_case

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data/literature-comparison-failures/n4g-interrupted-01'
CASE = RAW / 'bursty_jitter-low-s3010-b2-static-pNone'


class SkippedDecisionValidationTests(unittest.TestCase):
    def fixture(self):
        c = dict(origin_monotonic_ns=0, duration_us=10000000, decision_period_us=1000000)
        d = dict(scheduled_offset_us=1000000, cycle_started_ns=2000000000, cycle_finished_ns=2000001000,
                 skipped=True, reason='full_epoch_late', observation_index=2)
        return d, c

    def test_actual_failed_case_keeps_skipped_cycle_and_passes_full_checks(self):
        rpc = [json.loads(s) for s in (RAW / 'rpc-operations.jsonl').read_text().splitlines()]
        result = check_case(CASE, rpc)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['skipped_epochs'], 1)
        self.assertGreater(result['skipped_decision_checks'], 0)
        self.assertEqual(result['decision_count'], 10)

    def test_complete_late_period_is_valid(self):
        d, c = self.fixture(); self.assertEqual(skipped_errors(d, c), [])

    def test_early_or_reversed_period_rejected(self):
        d, c = self.fixture(); d['cycle_started_ns'] -= 1
        self.assertTrue(skipped_errors(d, c))
        d, c = self.fixture(); d['cycle_finished_ns'] = d['cycle_started_ns'] - 1
        self.assertTrue(skipped_errors(d, c))

    def test_skip_cannot_issue_observation_or_configuration(self):
        for field in ('view', 'reads', 'writes', 'selected', 'control_snapshot', 'solver_started_ns'):
            d, c = self.fixture(); d[field] = []
            self.assertTrue(skipped_errors(d, c), field)

    def test_missing_view_is_not_silently_accepted(self):
        d, c = self.fixture(); d['skipped'] = False
        self.assertTrue(skipped_errors(d, c))
        d, c = self.fixture(); d['scheduled_offset_us'] += 1
        self.assertTrue(skipped_errors(d, c))
