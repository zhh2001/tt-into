"""真实入口的自有费用、确认独立性与共同策略边界。"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'experiments')]
from control.matched_live import LivePlanner, OwnControl
from orchestration.accounted_engine import AccountedEngine
from orchestration.adaptive import AdaptiveSettings
from orchestration.matched_grid import METHODS
from orchestration.rolling import installation
from run_matched_reference import settings, workload
from rolling_workloads import MANAGEMENT, NODES, budget_for


def rpc(kind='update', register='TTEgress.time_threshold', before=100, after=200, success=True):
    return {'kind': kind, 'method': 'bm_register_write', 'arguments': [register, 1, 50000],
            'before_monotonic_ns': before, 'after_monotonic_ns': after, 'request_serialized_bytes': 94,
            'reply_serialized_bytes': 30, 'success': success}


class MatchedLiveTests(unittest.TestCase):
    def test_failure_is_paid_after_completion(self):
        account = OwnControl([rpc(success=False)], 0)
        self.assertFalse(account.snapshot(199).charges)
        charges = account.snapshot(200).charges
        self.assertEqual(sum(c.bytes for c in charges), 124)
        self.assertTrue(all(not c.success for c in charges))

    def test_real_reset_included_diagnostic_reset_excluded(self):
        log = [rpc('cold_start_reset', 'TTEgress.last_timestamp'),
               rpc('cold_start_reset', 'TTEgress.trace_generated'), rpc('diagnostic_read')]
        self.assertEqual(len(OwnControl(log, 0).snapshot(200).charges), 2)

    def test_cursor_excludes_previous_case(self):
        log = [rpc(before=0, after=10), rpc(before=100, after=200)]
        account = OwnControl(log, 100, 1)
        self.assertEqual({c.operation_id for c in account.snapshot(200).charges}, {'rpc-1'})

    def test_sync_idempotent_and_truncation_rejected(self):
        log = [rpc()]
        account = OwnControl(log, 0)
        self.assertEqual(account.snapshot(200), account.snapshot(200))
        log.clear()
        with self.assertRaises(ValueError):
            account.sync()

    def test_notice_is_separate_and_not_visible_early(self):
        account = OwnControl([], 100)
        account.notice('notice-1', b'{"version":1}', 300)
        self.assertFalse(account.snapshot(299).charges)
        window = account.snapshot(300).window(0, 201)
        self.assertEqual(window['bytes_by_layer'], {'api_serialized': 0, 'local_notice_payload': 13})

    def test_request_reply_cross_window(self):
        account = OwnControl([rpc(before=1999, after=2001)], 0)
        window = account.snapshot(2001).window(2000, 3000)
        self.assertEqual(window['bytes_by_layer']['api_serialized'], 30)

    def test_all_method_entries_and_static_hold(self):
        w, initial = workload('shared_priority', 11), (200000,) * 4
        engine = AccountedEngine(NODES, MANAGEMENT.routes, w.packets, installation(initial, MANAGEMENT, settings()), notices=w.notices)
        view = engine.run_until(3000000)
        for method in METHODS:
            planner = LivePlanner(method, MANAGEMENT, budget_for('shared_priority', 'high'), AdaptiveSettings(), initial)
            result, planned = planner.decide(view, engine.control_snapshot())
            self.assertLessEqual(len(planned), 6)
            self.assertLessEqual(len(result['candidate_scores']), 48)
            if method == 'static':
                self.assertFalse(planned)

    def test_cutoff_mismatch_and_unknown_method_rejected(self):
        with self.assertRaises(ValueError):
            LivePlanner('unknown', MANAGEMENT, budget_for('stable', 'high'), AdaptiveSettings(), (200000,) * 4)
        w = workload('stable', 11)
        engine = AccountedEngine(NODES, MANAGEMENT.routes, w.packets, installation((200000,) * 4, MANAGEMENT, settings()), notices=w.notices)
        view = engine.run_until(3000000)
        planner = LivePlanner('joint', MANAGEMENT, budget_for('stable', 'high'), AdaptiveSettings(), (200000,) * 4)
        with self.assertRaises(ValueError):
            planner.decide(view, OwnControl([], 0).snapshot(3000001000))


if __name__ == '__main__':
    unittest.main()
