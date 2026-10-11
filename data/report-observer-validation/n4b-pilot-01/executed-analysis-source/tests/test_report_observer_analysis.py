"""实际观察积分保留未知、尾段及权重，对伪造年龄拒绝。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
from analyze_report_observer import age_bounds
from check_report_observer_analysis import check_integrals


class ObserverAnalysisTests(unittest.TestCase):
    tasks = [{'object_id': {'node': 1, 'port': 2, 'queue': 0}, 'start_us': 0, 'end_us': 1000000,
              'target_us': 100000, 'weight': 1}]

    def sample(self, lower=100000000, upper=120000000):
        return {'node': 1, 'generation_lower_ns': 0, 'generation_upper_ns': 1000,
                'entered_lower_ns': lower, 'entered_upper_ns': upper}

    def test_no_sample_retains_full_unknown_loss(self):
        result = age_bounds(self.tasks, [], 0)
        self.assertEqual([result['loss_lower'], result['loss_upper']], [4, 4])
        self.assertGreater(check_integrals(self.tasks, [], 0, result), 0)

    def test_actual_entry_bounds_include_unknown_and_tail(self):
        samples = [self.sample()]
        result = age_bounds(self.tasks, samples, 0)
        self.assertAlmostEqual(result['loss_upper'], 3.608)
        self.assertLess(result['loss_lower'], result['loss_upper'])
        self.assertGreater(check_integrals(self.tasks, samples, 0, result), 0)

    def test_observation_after_task_window_does_not_backfill(self):
        samples = [self.sample(1100000000, 1200000000)]
        result = age_bounds(self.tasks, samples, 0)
        self.assertEqual(result['loss_upper'], 4)
        check_integrals(self.tasks, samples, 0, result)

    def test_unknown_weighted_task_stays_in_denominator(self):
        tasks = self.tasks + [dict(self.tasks[0], object_id={'node': 2, 'port': 2, 'queue': 0}, weight=4)]
        result = age_bounds(tasks, [self.sample()], 0)
        self.assertAlmostEqual(result['loss_upper'], (3.608 + 16) / 5)
        check_integrals(tasks, [self.sample()], 0, result)

    def test_fake_report_age_and_missing_samples_rejected(self):
        samples = [self.sample()]; result = age_bounds(self.tasks, samples, 0)
        with self.assertRaises(ValueError): check_integrals(self.tasks, samples, 0, dict(result, loss_lower=0))
        with self.assertRaises(ValueError): check_integrals(self.tasks, [], 0, result)
