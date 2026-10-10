"""独立检查器对不合法可用性和视图伪造的拒绝，以及完整运行顺序。"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
from check_network_calibration import check_prefixes
from run_network_calibration import make_order, PROFILES, METHODS
from test_into_live import row


class CalibrationTests(unittest.TestCase):
    def fixture(self, folder):
        raw = json.dumps(row()).encode() + b'\n'
        (folder / 'collector.jsonl').write_bytes(raw)
        (folder / 'result.json').write_text(json.dumps({'origin_monotonic_ns': 0}))
        for name in ('notice-send.jsonl', 'notices.jsonl'):
            (folder / name).write_text('')
        (folder / 'notice-schedule.json').write_text('[]')
        event = {'read_started_ns': 2000000, 'read_finished_ns': 2000100,
                 'ingest_started_ns': 2000200, 'available_ns': 2000300, 'cutoff_ns': 2001000,
                 'file_bytes': len(raw), 'prefix_bytes': len(raw), 'row_count': 1,
                 'prefix_sha256': hashlib.sha256(raw).hexdigest(), 'reports_before': 0, 'reports_after': 1}
        decision = {'cycle_started_ns': 1999999, 'cutoff_ns': 2001000, 'solver_started_ns': 2002000,
                    'availability': event, 'view': {'reports': [{'received_us': 1000,
                    'flow': {'session': 20, 'tag': 1001}, 'records': [{'node': 1, 'hop': 1,
                    'generated_local_us': 50, 'queue_packets': 4}]}], 'cache': [
                    {'object_id': {'node': 1, 'port': 2, 'queue': 0}, 'generated_local_us': 50,
                     'received_us': 1000, 'queue_packets': 4}]}}
        return decision

    def check(self, mutate=None):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            d = self.fixture(folder)
            if mutate:
                mutate(d)
            (folder / 'decisions.jsonl').write_text(json.dumps(d) + '\n')
            (folder / 'availability.jsonl').write_text(json.dumps(d['availability']) + '\n')
            return check_prefixes(folder)

    def test_independent_valid_prefix(self):
        self.assertGreater(self.check(), 0)

    def test_solver_before_information_available_rejected(self):
        with self.assertRaisesRegex(ValueError, '读取'):
            self.check(lambda d: d.update(solver_started_ns=2000250))

    def test_forged_prefix_digest_rejected(self):
        with self.assertRaises(ValueError):
            self.check(lambda d: d['availability'].update(prefix_sha256='0' * 64))

    def test_missing_or_added_view_report_rejected(self):
        for mutate in (lambda d: d['view'].update(reports=[]),
                       lambda d: d['view']['reports'].append(copy.deepcopy(d['view']['reports'][0]))):
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.check(mutate)

    def test_three_blocks_complete_and_deterministic(self):
        profiles = [s + '-' + b for s, b in PROFILES]
        order = make_order(METHODS, profiles, 3)
        self.assertEqual(order, make_order(METHODS, profiles, 3))
        self.assertEqual(len(order), 96)
        self.assertEqual(len({r['name'] for r in order}), 96)
        for block in (1, 2, 3):
            cells = [r for r in order if r['block'] == block]
            self.assertEqual(len(cells), 32)
            self.assertEqual(len({(r['scenario'], r['budget_level'], r['method']) for r in cells}), 32)
        self.assertNotEqual([r['method'] for r in order[:32]], [r['method'] for r in order[32:64]])


if __name__ == '__main__':
    unittest.main()
