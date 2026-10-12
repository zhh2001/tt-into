"""PCAP 精度、半开窗口及生成微秒截断的区间边界。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
from network_entry_bounds import generation_bounds, member_bounds, wall_interval


class NetworkEntryBoundsTests(unittest.TestCase):
    def test_half_open_window(self):
        self.assertEqual(member_bounds(100, 100, 100, 200), (True, True))
        self.assertEqual(member_bounds(200, 200, 100, 200), (False, False))
        self.assertEqual(member_bounds(99, 100, 100, 200), (False, True))
        self.assertEqual(member_bounds(199, 200, 100, 200), (False, True))

    def test_invalid_interval_rejected(self):
        with self.assertRaises(ValueError):
            member_bounds(101, 100, 100, 200)

    def test_wall_interval_intersection(self):
        samples = [{'before_monotonic_ns': 100, 'wall_ns': 1000, 'after_monotonic_ns': 104},
                   {'before_monotonic_ns': 200, 'wall_ns': 1099, 'after_monotonic_ns': 201}]
        bound = wall_interval(samples)
        self.assertTrue(bound['compatible'])
        self.assertEqual((bound['lower_ns'], bound['upper_ns']), (898, 899))
        samples[1]['wall_ns'] += 10
        self.assertFalse(wall_interval(samples)['compatible'])

    def test_generation_truncation_retains_true_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            true_times = [101900, 201100]
            send = [{'packet_id': i, 'send_before_monotonic_ns': t - 50} for i, t in enumerate(true_times)]
            business = [{'kind': 'frame', 'src': '10.0.0.1', 'packet_id': i, 'receive_monotonic_ns': t + 50} for i, t in enumerate(true_times)]
            reports = [{'ether_type': 0x88b6, 'packet_id': i, 'receive_monotonic_ns': t + 100,
                        'records': [{'switch_id': 1, 'generated_us': (t - 100000) // 1000}]} for i, t in enumerate(true_times)]
            for name, rs in (('send', send), ('destination', business), ('collector', reports)):
                (d / f'{name}.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rs))
            (d / 'tasks.json').write_text(json.dumps([{'object_id': {'node': 1, 'port': 2, 'queue': 0},
                'start_us': 100, 'end_us': 250, 'target_us': 100, 'weight': 1}]))
            bounds = generation_bounds(d, {'origin_monotonic_ns': 0})
            self.assertTrue(bounds['local_clock_offset']['1']['compatible'])
            for r, t in zip(bounds['intervals'], true_times):
                self.assertLessEqual(r['generation_lower_ns'], t)
                self.assertGreaterEqual(r['generation_upper_ns'], t)
            self.assertLessEqual(bounds['loss_lower'], bounds['loss_upper'])


if __name__ == '__main__':
    unittest.main()
