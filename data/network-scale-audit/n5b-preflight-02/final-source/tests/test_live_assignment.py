"""真实报告适配的因果性、时钟不兼容和最优分配边界。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from orchestration.live_assignment import LiveAssignment, ReceivedState


def report(pid, generated, received):
    return {'packet_id': pid, 'receive_monotonic_ns': received,
            'records': [{'switch_id': 1, 'generated_us': generated}]}


class ReceivedTests(unittest.TestCase):
    def test_unreceived_and_unjoined_reports_are_invisible(self):
        state = ReceivedState()
        state.ingest([report(1, 100, 120000)], {1: {'send_before_monotonic_ns': 100000}}, 119999)
        self.assertFalse(state.seen)
        state.ingest([report(1, 100, 120000)], {}, 120000)
        self.assertIsNone(state.snapshot(120000)[0]['age_upper_us'])

    def test_old_generation_cannot_rewind_latest(self):
        state = ReceivedState()
        sends = {1: {'send_before_monotonic_ns': 200000}, 2: {'send_before_monotonic_ns': 100000}}
        state.ingest([report(1, 200, 220000), report(2, 100, 230000)], sends, 230000)
        self.assertEqual(state.latest[1], 200)
        value = state.snapshot(250000)[0]
        self.assertEqual((value['age_lower_us'], value['age_upper_us']), (30, 50))

    def test_fixed_offset_incompatibility_is_detected(self):
        state = ReceivedState()
        state.ingest([report(1, 100, 110000)], {1: {'send_before_monotonic_ns': 100000}}, 110000)
        with self.assertRaises(ValueError):
            state.ingest([report(2, 200, 310000)], {2: {'send_before_monotonic_ns': 300000}}, 310000)

    def test_duplicate_report_does_not_change_interval(self):
        state = ReceivedState()
        r, sends = report(1, 100, 110000), {1: {'send_before_monotonic_ns': 100000}}
        state.ingest([r], sends, 110000)
        before = state.snapshot(120000)
        state.ingest([r], sends, 120000)
        self.assertEqual(before, state.snapshot(120000))

    def test_conservative_expiry_changes_dependency_objective(self):
        unknown = [{'node': n, 'age_upper_us': None} for n in (1, 2, 3)]
        driver = LiveAssignment('intopp', {'lookahead_us': 0, 'flow_order': [1, 2, 3]}, 1)
        initial = driver.decide((100, 100, 100), unknown)
        equal = driver.decide((100, 100, 100), [{'node': n, 'age_upper_us': 100} for n in (1, 2, 3)])
        expired = driver.decide((100, 100, 100), [{'node': n, 'age_upper_us': 100.001} for n in (1, 2, 3)])
        self.assertEqual((initial['objective'], equal['objective'], expired['objective']), (5, 0, 5))
        self.assertEqual(equal['variation_bytes'], 0)

    def test_both_capacities_preserve_selected_assignment_and_queue(self):
        unknown = [{'node': n, 'age_upper_us': None} for n in (1, 2, 3)]
        for capacity, order, shared in ((1, [1, 2, 3], 3), (2, [2, 1, 3], 2)):
            driver = LiveAssignment('dinto', {'tradeoff': 1., 'average_change_bytes': 0.,
                                            'gain': 'static', 'flow_order': order}, capacity)
            first = driver.decide((220000, 220000, 260000), unknown)
            second = driver.decide((220000, 220000, 260000), unknown)
            self.assertEqual([r['flow'] for r in first['assignment']], [1, 2, shared])
            self.assertEqual(first['variation_bytes'], 36)
            self.assertEqual(second['variation_bytes'], 0)
            self.assertEqual(second['queue_after'], 36)


if __name__ == '__main__':
    unittest.main()
