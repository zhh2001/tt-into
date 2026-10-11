"""原生容量、期限边界、TT投影和确认结算的独立检查。"""
from dataclasses import replace
from itertools import product
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'experiments')]
from orchestration.literature import LiteratureSettings, LiteratureProxy, native_rows, dependency_due
from orchestration.control_ledger import ControlLedger
from orchestration.matched_grid import ControlCaps, settings
from orchestration.adaptive import AdaptiveSettings
from orchestration.event_engine import EventEngine
from orchestration.rolling import installation, changes
from control.literature_live import LiteraturePlanner
from rolling_workloads import MANAGEMENT, FLOWS, NODES, budget_for
from test_adaptive import view_at


def proxy(method='d_into_tt', view=None):
    view = view or view_at(3000000)
    return LiteratureProxy(view, MANAGEMENT, budget_for('stable', 'high'), settings(), AdaptiveSettings(),
        ControlLedger().snapshot(view.now_us * 1000), ControlCaps(1200, 128), temporal=False, guarded=False,
        method=method, literature=LiteratureSettings(), queue=0)


class LiteratureTests(unittest.TestCase):
    def test_native_p3_against_object_assignment_oracle(self):
        keys = MANAGEMENT.keys
        for queue, scale, cap in product((0, 1, 24), (1, 144), (0, 12, 24)):
            benefits = {1: 1, 2: 2, 3: 4}
            previous = (True, False, True, False)
            expected = {}
            for carriers in product((None, FLOWS[0]), (None, FLOWS[1]), (None, *FLOWS)):
                enabled = {(n, f) for n, f in enumerate(carriers, 1) if f is not None}
                if any(12 * sum(f == flow for _, f in enabled) > cap for flow in FLOWS):
                    continue
                bits = tuple(k in enabled for k in keys)
                gain = sum(benefits[n] for n, _ in enabled)
                delta = 12 * len(set(k for k, b in zip(keys, previous) if b) ^ enabled)
                expected[bits] = queue * delta - scale * gain
            actual = native_rows(keys, benefits, previous, queue, scale, dict.fromkeys(FLOWS, cap))
            self.assertEqual({r['assignment']: r['objective'] for r in actual}, expected)

    def test_strict_elapsed_deadline_and_unknown(self):
        self.assertFalse(dependency_due(150000, 150000, 1000000))
        self.assertTrue(dependency_due(150001, 150000, 1000000))
        self.assertTrue(dependency_due(None, 150000, 1000000))

    def test_early_variant_has_declared_margin(self):
        self.assertTrue(dependency_due(1, 150000, 1000000, .5))
        self.assertFalse(dependency_due(1, 150000, 1000000, 0))

    def test_tt_samples_match_event_capacity_for_all_binary_masks(self):
        p = proxy()
        for bits in product((False, True), repeat=4):
            config = tuple(100000 if b else None for b in bits)
            engine = EventEngine(NODES, MANAGEMENT.routes, p.packets, installation(config, MANAGEMENT, p.settings), capacity=1)
            engine.run_until(p.view.now_us)
            for key, stamp in p.last_estimate.items():
                # 不同时钟实例按本地偏移设置，当前fixture没有历史样本。
                engine.gates[key].last_local_us = (stamp + engine.nodes[key[0]].clock_offset_us) % 2**48
            result = engine.run()
            row = p.evaluate(config)
            for item in row['literature_inputs']:
                self.assertEqual(item['predicted_samples'], sum(r['object_id'].node == item['node'] and
                    r['received_us'] < p.view.now_us + p.settings.horizon_us for r in result['receipts']))
            self.assertTrue(all(len(r['nodes']) <= 1 for r in row['predicted_packet_choices']))

    def test_objective_uses_frequency_not_continuous_age(self):
        p = proxy()
        r = p.evaluate((100000, None, 100000, None))
        self.assertAlmostEqual(r['literature_objective'], -144 * r['information'])
        self.assertEqual(r['information'], 2)

    def test_shared_state_not_credited_twice(self):
        p = proxy()
        r = p.evaluate((None, 100000, None, 100000))
        self.assertFalse(r['assignment_admissible'])
        self.assertLessEqual(r['information'], 2)

    def test_missing_statistics_hold_common_initialization(self):
        v = replace(view_at(3000000), counts=())
        planner = LiteraturePlanner('d_into_tt', MANAGEMENT, budget_for('stable', 'high'), AdaptiveSettings(), (50000,) * 4)
        chosen, planned = planner.decide(v, ControlLedger().snapshot(v.now_us * 1000))
        self.assertEqual(planned, [])
        self.assertEqual(chosen['selected']['config'], (50000,) * 4)

    def test_failed_request_does_not_settle_virtual_queue(self):
        v = view_at(3000000)
        planner = LiteraturePlanner('d_into_tt', MANAGEMENT, budget_for('stable', 'high'), AdaptiveSettings(), (50000,) * 4,
                                    LiteratureSettings(average_change_bytes=0))
        planner.decide(v, ControlLedger().snapshot(v.now_us * 1000))
        failed = replace(v.confirmations[1], received_us=3500000, version=2, value=False, success=False)
        v2 = replace(v, now_us=4000000, confirmations=v.confirmations + (failed,))
        chosen, _ = planner.decide(v2, ControlLedger().snapshot(v2.now_us * 1000))
        self.assertEqual(chosen['literature_state']['settled_change_bytes'], 0)
        self.assertEqual(planner.queue, 0)

    def test_confirmed_partial_write_settles_only_actual_bit(self):
        v = view_at(3000000)
        planner = LiteraturePlanner('d_into_tt', MANAGEMENT, budget_for('stable', 'high'), AdaptiveSettings(), (50000,) * 4,
                                    LiteratureSettings(average_change_bytes=0))
        planner.decide(v, ControlLedger().snapshot(v.now_us * 1000))
        success = replace(v.confirmations[1], received_us=3500000, version=2, value=False, success=True)
        v2 = replace(v, now_us=4000000, confirmations=v.confirmations + (success,))
        chosen, _ = planner.decide(v2, ControlLedger().snapshot(v2.now_us * 1000))
        self.assertEqual(chosen['literature_state']['settled_change_bytes'], 12)
        self.assertEqual(planner.queue, 12)

    def test_threshold_change_paid_but_not_assignment_delta(self):
        p = proxy()
        r = p.evaluate((100000,) * 4)
        self.assertEqual(r['assignment_change_bytes'], 0)
        self.assertEqual(len(changes(r['config'], p.fields, MANAGEMENT)), 4)
        self.assertGreater(r['cost']['control_serialized_bytes_by_kind']['update'], 0)

    def test_both_live_methods_respect_limits_and_unique_carriers(self):
        v = view_at(3000000)
        for method in ('intopp_due', 'd_into_tt'):
            planner = LiteraturePlanner(method, MANAGEMENT, budget_for('stable', 'high'), AdaptiveSettings(), (50000,) * 4)
            result, planned = planner.decide(v, ControlLedger().snapshot(v.now_us * 1000))
            self.assertLessEqual(len(planned), 6)
            self.assertLessEqual(len(result['candidate_scores']), 48)
            self.assertTrue(result['selected']['forecast']['assignment_admissible'])

    def test_future_reports_counts_confirmations_cache_notices_rejected(self):
        v = view_at(3000000, times=(100000,))
        for name, field in (('reports', 'received_us'), ('counts', 'available_us'), ('confirmations', 'received_us'),
                            ('notifications', 'received_us')):
            with self.subTest(name=name), self.assertRaises(ValueError):
                proxy(view=replace(v, **{name: (replace(getattr(v, name)[0], **{field: v.now_us + 1}),)}))

    def test_invalid_parameters_rejected(self):
        for kwargs in (dict(value_scale=0), dict(value_scale=float('nan')), dict(average_change_bytes=-1), dict(early_fraction=2)):
            with self.assertRaises(ValueError):
                LiteratureSettings(**kwargs)


if __name__ == '__main__': unittest.main()
