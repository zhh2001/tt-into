"""完整事件中的分层通知、自有调用完成与未来后缀边界。"""
from dataclasses import replace
import copy
from pathlib import Path
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'src'), str(Path(__file__).resolve().parents[1] / 'experiments')]
from orchestration.accounted_engine import AccountedEngine, ManagedNotice, layered_costs, layered_excess, managed_notice
from orchestration.adaptive import AdaptiveSettings
from orchestration.event_schema import Read, Write
from orchestration.matched_grid import settings
from orchestration.rolling import installation
from rolling_workloads import FLOWS, MANAGEMENT, NODES, budget_for
from run_matched_reference import caps_for, plain, simulate, workload
from check_matched_reference import check_layered


def fees():
    return {k: plain(MANAGEMENT.read_cost if k == 'read' else MANAGEMENT.cost_for_write(k))
            for k in ('read', 'enabled', 'threshold_us')}


def manifest():
    return plain({'settings': settings(), 'notice_budget_bytes_per_s': 128,
                  'topology': {'nodes': NODES, 'routes': [{'flow': f, 'objects': r} for f, r in MANAGEMENT.routes.items()]}})


class AccountedEngineTests(unittest.TestCase):
    def engine(self, writes=(), reads=()):
        w = workload('stable', 11)
        return AccountedEngine(NODES, MANAGEMENT.routes, (), writes, reads, notices=w.notices)

    def test_notification_cost_and_visibility_use_polled_arrival(self):
        e = self.engine()
        self.assertFalse(e.run_until(0).notifications)
        v = e.run_until(1000000)
        self.assertEqual(v.notifications[0].received_us, 1000000)
        r = e.result()
        self.assertEqual(r['control_serialized_bytes_by_kind']['observation'], 0)
        self.assertEqual(r['local_notice_payload_bytes'], 190)
        self.assertEqual(e.control_snapshot().window(0, 2000000000)['bytes_by_layer']['local_notice_payload'], 190)

    def test_no_future_length_before_rpc_completes(self):
        op = Read('r', FLOWS[0], 1, 10, 50, 100, MANAGEMENT.read_cost)
        e = self.engine(reads=(op,))
        e.run_until(50)
        self.assertEqual(e.result()['control_serialized_bytes_by_kind']['observation'], 81)
        self.assertFalse(e.control_snapshot().charges)
        e.run_until(100)
        self.assertEqual(e.control_snapshot().window(0, 101000)['bytes_by_layer']['api_serialized'], 121)
        self.assertTrue(all('合成' in r.provenance for r in e.control_snapshot().charges))

    def test_failed_model_rpc_keeps_own_fee(self):
        op = Write('failed', 1, FLOWS[0], 'enabled', True, 2, 10, 50, 100, MANAGEMENT.cost_for_write('enabled'), success=False)
        e = self.engine(writes=(op,))
        e.run_until(100)
        self.assertFalse(e.completed_calls[0]['success'])
        self.assertEqual(e.control_snapshot().window(0, 101000)['bytes_by_layer']['api_serialized'], 119)

    def test_request_and_reply_split_at_window_boundary(self):
        e = self.engine(reads=(Read('r', FLOWS[0], 1, 1999990, 2000000, 2000010, MANAGEMENT.read_cost),))
        r = e.run()
        cost = layered_costs(r['cost_events'], 2000000, 3000000)
        self.assertEqual(cost['control_serialized_bytes_by_kind']['observation'], 40)
        self.assertEqual(cost['local_notice_payload_bytes'], 0)

    def test_notice_does_not_shift_task_activation(self):
        w = workload('switch', 11)
        self.assertEqual([n.available_us for n in w.notices], [1000000, 6000000])
        self.assertEqual(min(t.start_us for t in w.tasks if t.task_id.startswith('task-2')), 5000000)

    def test_bad_payload_and_duplicate_notices_rejected(self):
        n = workload('stable', 11).notices[0]
        with self.assertRaises(ValueError):
            replace(n, payload=b'{}')
        with self.assertRaises(ValueError):
            AccountedEngine(NODES, MANAGEMENT.routes, (), notices=(n, n))

    def test_notice_budget_separate_from_api(self):
        e = self.engine()
        c = layered_costs(e.run()['cost_events'], 0, 2000000)
        budget = budget_for('stable', 'high')
        self.assertEqual(sum(c['control_serialized_bytes_by_kind'].values()), 0)
        self.assertGreater(layered_excess(c, budget, replace(caps_for(budget), notice_payload_bytes_per_s=1)), 0)

    def test_static_has_no_paid_source_polling(self):
        row, raw = simulate(workload('switch', 11), budget_for('switch', 'high'), (200000,) * 4,
                            'static', AdaptiveSettings(), MANAGEMENT)
        self.assertTrue(all(not d['reads'] and not d['writes'] for d in raw['decisions']))
        self.assertEqual(sum(row['cost']['control_serialized_bytes_by_kind'].values()), 0)
        self.assertEqual(row['cost']['local_notice_payload_bytes'], 190)

    def test_future_suffix_cannot_change_controller_prefix(self):
        first = workload('switch', 11)
        altered = replace(first, packets=tuple(p for p in first.packets if p.hops[0].ingress_us < 5000000), notices=first.notices[:1])
        a = simulate(first, budget_for('switch', 'high'), (200000,) * 4, 'phase_guard', AdaptiveSettings(), MANAGEMENT)[1]
        b = simulate(altered, budget_for('switch', 'high'), (200000,) * 4, 'phase_guard', AdaptiveSettings(), MANAGEMENT)[1]
        for x, y in zip(a['decisions'][:5], b['decisions'][:5]):
            for key in ('view', 'selected', 'control_snapshot', 'budget_diagnostic'):
                self.assertEqual(x[key], y[key])

    def test_full_independent_contract_accepts_all_methods(self):
        from orchestration.matched_grid import METHODS
        w = workload('bursty_jitter', 11)
        budget = budget_for('bursty_jitter', 'low')
        for method in METHODS:
            params = AdaptiveSettings()
            _, r = simulate(w, budget, (200000,) * 4, method, params, MANAGEMENT)
            counts = check_layered(plain(w), plain(r), plain(budget), (200000,) * 4, manifest(), fees(), plain(params))
            self.assertEqual(counts['decisions'], 10)

    def test_corrupted_known_prefix_rejected(self):
        w = workload('stable', 11)
        b = budget_for('stable', 'high')
        raw = plain(simulate(w, b, (200000,) * 4, 'phase_guard', AdaptiveSettings(), MANAGEMENT)[1])
        broken = copy.deepcopy(raw)
        broken['decisions'][1]['control_snapshot']['charges'][0]['bytes'] += 1
        with self.assertRaises(ValueError):
            check_layered(plain(w), broken, plain(b), (200000,) * 4, manifest(), fees(), plain(AdaptiveSettings()))

    def test_notice_recategorized_as_rpc_rejected(self):
        w = workload('stable', 11)
        b = budget_for('stable', 'high')
        raw = plain(simulate(w, b, (200000,) * 4, 'static', AdaptiveSettings(), MANAGEMENT)[1])
        item = next(e for e in raw['result']['cost_events'] if e['resource'] == 'local_notice')
        item.update(resource='control', kind='observation', basis='serialized_rpc')
        with self.assertRaises(ValueError):
            check_layered(plain(w), raw, plain(b), (200000,) * 4, manifest(), fees(), plain(AdaptiveSettings()))


if __name__ == '__main__':
    unittest.main()
