"""验证实际文件可用性与不完整尾行、未来记录和重复前缀。"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from collector.available_prefix import ingest_prefix
from collector.into_state import IntoState
from test_into_live import ROUTES, row


class AvailablePrefixTests(unittest.TestCase):
    def ingest(self, values, times=(2000000, 2000100, 2000200, 2000300), state=None, tail=b''):
        state = IntoState(ROUTES, 0) if state is None else state
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'reports.jsonl'
            raw = b''.join(json.dumps(r).encode() + b'\n' for r in values) + tail
            path.write_bytes(raw)
            ticks = iter(times)
            event = ingest_prefix(state, path, lambda: next(ticks))
        return state, event, raw

    def test_complete_prefix_retains_partial_tail(self):
        state, event, raw = self.ingest([row()], tail=b'{"partial":')
        self.assertEqual(event['file_bytes'], len(raw))
        self.assertEqual(event['prefix_sha256'], hashlib.sha256(raw[:event['prefix_bytes']]).hexdigest())
        self.assertEqual(event['row_count'], 1)
        self.assertEqual(event['reports_after'], 1)
        self.assertGreater(event['available_ns'], event['read_finished_ns'])
        self.assertEqual(state.reports[0].received_us, 1000)

    def test_incomplete_only_does_not_enter_state(self):
        state, event, _ = self.ingest([], tail=b'{')
        self.assertEqual(event['prefix_bytes'], 0)
        self.assertFalse(state.reports)

    def test_future_physical_receive_rejected(self):
        with self.assertRaisesRegex(ValueError, '未来'):
            self.ingest([row()], times=(0, 999999, 2000000, 2000300))

    def test_repeated_prefix_preserves_sample_cache_time(self):
        state, _, _ = self.ingest([row()])
        _, event, _ = self.ingest([row()], state=state, times=(3000000, 3000100, 3000200, 3000300))
        self.assertEqual(event['reports_before'], event['reports_after'])
        self.assertEqual(state.cache[next(iter(state.cache))].received_us, 1000)

    def test_read_and_ingest_clock_reversal_rejected(self):
        for ticks in ((2000000, 1999999, 2000200, 2000300),
                      (2000000, 2000100, 2000099, 2000300),
                      (2000000, 2000100, 2000200, 2000199)):
            with self.subTest(ticks=ticks), self.assertRaises(ValueError):
                self.ingest([row()], times=ticks)

    def test_earlier_physical_receive_missing_from_read_is_not_invented(self):
        state, event, _ = self.ingest([])
        self.assertEqual(event['reports_after'], 0)
        self.assertFalse(state.snapshot(2000300, []).reports)
        self.ingest([row()], state=state, times=(3000000, 3000100, 3000200, 3000300))
        self.assertEqual(len(state.reports), 1)


if __name__ == '__main__':
    unittest.main()
