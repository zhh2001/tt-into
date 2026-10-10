"""动态控制的因果边界、容量适配与有限网格核对。"""
from dataclasses import replace
from pathlib import Path
import copy
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'experiments'))
from collector.tagged_codec import WireRecord
from orchestration.event_engine import EventEngine
from orchestration.event_observation import (CacheState, Confirmation, CountObservation, DemandNotification,
                                            OnlineView, ReceivedReport)
from orchestration.event_schema import Demand, FlowId, Hop, Node, Notice, ObjectId, Packet, Read, RpcCost, Write
from orchestration.rolling import (OpportunityProxy, Settings, changes, choose, configurations, current_demands,
                                   exact_proxy, fields_from_confirmations, installation)
from rolling_rpc_cost import calibration
from rolling_workloads import MANAGEMENT, NODES, TEST_SEEDS, TRAIN_SEEDS, budget_for, generate
from run_rolling_reference import simulate
from run_rolling_reference import plain
from check_rolling_reference import check_raw, clipped_loss

F = FlowId(2, 7)
O = ObjectId(1)
C = RpcCost(10, 4, '确定性合成检查')


class DynamicTests(unittest.TestCase):
    def engine(self, packets=(), notices=()):
        return EventEngine([Node(1)], {F: (O,)}, packets, notices=notices)

    def test_dynamic_calls_cannot_be_backfilled_or_created_before_clock(self):
        engine = self.engine()
        write = Write('w', 1, F, 'enabled', True, 1, 0, 1, 2, C)
        with self.assertRaises(ValueError):
            engine.submit_write(write)
        engine.run_until(0)
        with self.assertRaises(ValueError):
            engine.submit_write(write)
        with self.assertRaises(ValueError):
            engine.submit_read(Read('r', F, 1, -1, 1, 2, C))

    def test_dynamic_inflight_write_visible_only_after_ack_and_charged(self):
        p = Packet(1, F, (Hop(O, 'e1', 0, 10),), 11, (12,))
        engine = self.engine((p,))
        engine.run_until(1)
        w = Write('on', 1, F, 'enabled', True, 2, 2, 5, 20, C)
        engine.submit_write(w)
        self.assertEqual(engine.run_until(15).confirmations, ())
        self.assertEqual(engine.result()['selected'][1], [1])
        self.assertEqual(engine.run_until(20).confirmations[0].write_id, 'on')
        self.assertEqual(engine.result()['control_serialized_bytes_by_kind']['update'], 14)

    def test_duplicate_and_non_source_dynamic_calls_rejected(self):
        second = ObjectId(2)
        engine = EventEngine([Node(1), Node(2)], {F: (O, second)}, ())
        engine.run_until(0)
        r = Read('r', F, 1, 1, 2, 3, C)
        engine.submit_read(r)
        with self.assertRaises(ValueError):
            engine.submit_read(r)
        with self.assertRaises(ValueError):
            engine.submit_read(replace(r, read_id='other', node=2))
        w = Write('w', 1, F, 'enabled', True, 1, 1, 2, 3, C)
        engine.submit_write(w)
        with self.assertRaises(ValueError):
            engine.submit_write(w)

    def test_paid_dynamic_count_hidden_until_response(self):
        p = Packet(1, F, (Hop(O, 'e1', 2, 3),), 4, ())
        engine = self.engine((p,))
        engine.run_until(0)
        engine.submit_read(Read('r', F, 1, 1, 3, 5, C))
        self.assertEqual(engine.run_until(4).counts, ())
        self.assertEqual(engine.run_until(5).counts[0].count, 1)
        self.assertEqual(engine.result()['control_serialized_bytes_by_kind']['observation'], 14)

    def test_notice_is_unavailable_before_delivery_and_pays_once(self):
        notice = Notice('n', 10, 2, (Demand(O, 100, 2),), C)
        engine = self.engine(notices=(notice,))
        self.assertEqual(engine.run_until(9).notifications, ())
        self.assertEqual(current_demands(engine.run_until(10)), notice.demands)
        engine.run_until(20)
        self.assertEqual(engine.result()['control_serialized_bytes_by_kind']['observation'], 14)

    def test_failed_dynamic_update_keeps_gate_but_still_pays(self):
        engine = self.engine()
        engine.run_until(0)
        engine.submit_write(Write('failed', 1, F, 'enabled', True, 1, 1, 2, 3, C, success=False))
        engine.run()
        self.assertFalse(engine.gates[1, F].enabled)
        self.assertFalse(engine.view().confirmations[0].success)
        self.assertEqual(engine.result()['control_serialized_bytes_by_kind']['update'], 14)

    def test_notice_registration_and_demand_schema(self):
        for kwargs in ({'target_us': 0}, {'target_us': 1, 'weight': float('nan')}, {'target_us': 1, 'active': 1}):
            with self.assertRaises(ValueError):
                Demand(O, **kwargs)
        with self.assertRaises(ValueError):
            self.engine(notices=(Notice('n', 0, 1, (Demand(ObjectId(2), 1),), C),))
        notice = Notice('n', 0, 1, (Demand(O, 1),), C)
        with self.assertRaises(ValueError):
            self.engine(notices=(notice, notice))


class RollingTests(unittest.TestCase):
    def view(self, config=(200000, 50000, 200000, 50000)):
        confirmations = tuple(Confirmation(w.write_id, w.ack_us, w.node, w.flow, w.field, w.value, w.version, True)
                              for w in installation(config, MANAGEMENT, Settings()))
        counts = tuple(CountObservation(f'r-{i}-{f.tag}', f, i * 1000000, i * 1000000 + 101, i * 8)
                       for i in range(2) for f in sorted(MANAGEMENT.routes))
        workload = generate('stable', 11)
        notifications = (DemandNotification(0, 1, workload.notices[0].demands),)
        return OnlineView(2000000, (), counts, confirmations, (), notifications)

    def proxy(self, view=None, settings=None):
        return OpportunityProxy(view or self.view(), MANAGEMENT, budget_for('stable', 'low'), settings or Settings())

    def test_failure_or_old_ack_does_not_replace_confirmed_version(self):
        view = self.view()
        first = view.confirmations[0]
        newer = replace(first, write_id='new', version=3, value=1000000, received_us=100)
        stale = replace(first, write_id='old', version=2, value=50000, received_us=200)
        failed = replace(first, write_id='failed', version=4, value=200000, received_us=300, success=False)
        fields = fields_from_confirmations(replace(view, confirmations=view.confirmations + (newer, stale, failed)), MANAGEMENT)
        self.assertEqual(fields[first.node, first.flow]['threshold_us'], 1000000)

    def test_reenable_writes_threshold_first_and_disable_preserves_it(self):
        fields = fields_from_confirmations(self.view((None, None, None, None)), MANAGEMENT)
        diffs = changes((1000000, None, None, None), fields, MANAGEMENT)
        self.assertEqual([r[1] for r in diffs], ['threshold_us', 'enabled'])
        fields = fields_from_confirmations(self.view(), MANAGEMENT)
        diffs = changes((None, 50000, 200000, 50000), fields, MANAGEMENT)
        self.assertEqual(diffs, [(MANAGEMENT.keys[0], 'enabled', False)])

    def test_new_demand_deactivation_and_stale_version(self):
        view = self.view()
        obj = view.notifications[0].demands[0].object_id
        newer = DemandNotification(100, 3, (Demand(obj, 1, active=False),))
        stale = DemandNotification(200, 2, (Demand(obj, 10),))
        active = current_demands(replace(view, notifications=view.notifications + (newer, stale)))
        self.assertNotIn(obj, {d.object_id for d in active})

    def test_future_reports_counts_notices_and_cache_are_rejected(self):
        view = self.view()
        flow = next(iter(MANAGEMENT.routes))
        for attr, values in [('reports', (ReceivedReport(view.now_us + 1, flow, (WireRecord(1, 1, 0, 0),)),)),
                             ('cache', (CacheState(O, 0, view.now_us + 1, 0),)),
                             ('counts', (replace(view.counts[0], available_us=view.now_us + 1),)),
                             ('notifications', (replace(view.notifications[0], received_us=view.now_us + 1),))]:
            with self.assertRaises(ValueError):
                self.proxy(replace(view, **{attr: values}))

    def test_two_paid_counts_required_and_zero_rate_means_no_opportunities(self):
        view = self.view()
        proxy = self.proxy(replace(view, counts=view.counts[:2]))
        self.assertFalse(proxy.ready)
        self.assertEqual(choose(proxy, 'joint', proxy.current)['reason'], 'insufficient_observations')
        zero = self.proxy(replace(view, counts=tuple(replace(c, count=0) for c in view.counts)))
        self.assertTrue(zero.ready)
        self.assertEqual(zero.packets, ())
        self.assertEqual(zero.evaluate(zero.current)['predicted_loss'], 4.0)

    def test_capacity_changes_loss_but_local_objective_keeps_uncapped_supply(self):
        config = (50000,) * 4
        one = self.proxy(settings=Settings(capacity=1)).evaluate(config)
        two = self.proxy(settings=Settings(capacity=2)).evaluate(config)
        self.assertGreater(one['predicted_loss'], two['predicted_loss'])
        self.assertEqual(one['local_loss'], two['local_loss'])
        self.assertLess(one['cost']['inband_per_link_bytes']['e3'], two['cost']['inband_per_link_bytes']['e3'])

    def test_disabled_configuration_still_pays_header_floor_and_reads(self):
        row = self.proxy().evaluate((None,) * 4)
        self.assertEqual(row['cost']['inband_per_link_bytes'], {'e1': 96, 'e2': 96, 'e3': 192})
        self.assertEqual(row['cost']['export_attempt_bytes'], 0)
        self.assertEqual(row['cost']['control_serialized_bytes_by_kind']['observation'], 242)

    def test_grid_and_search_limits_checked_against_enumeration(self):
        proxy = self.proxy()
        self.assertEqual(len(list(configurations(MANAGEMENT, Settings()))), 256)
        selected = choose(proxy, 'joint', proxy.current)
        self.assertLessEqual(selected['evaluated_candidates'], 48)
        self.assertLessEqual(selected['forecast']['update_rpc_count'], 6)
        exact = exact_proxy(self.proxy())
        self.assertGreaterEqual(proxy.rank(selected['forecast']), proxy.rank(exact))

    def test_update_limit_zero_forbids_any_changes(self):
        proxy = self.proxy(settings=Settings(max_update_rpcs=0))
        for method in ('feedback', 'local', 'joint'):
            selected = choose(proxy, method, proxy.current)
            self.assertEqual(selected['config'], proxy.current)

    def test_future_suffix_does_not_change_earlier_decisions(self):
        original = generate('switch', 101)
        cut = tuple(p for p in original.packets if p.hops[0].ingress_us < 5000000)
        altered = replace(original, packets=cut, notices=original.notices[:1])
        trained = (200000, 50000, 200000, 50000)
        _, first = simulate(original, budget_for('switch', 'high'), trained, 'joint', Settings())
        _, second = simulate(altered, budget_for('switch', 'high'), trained, 'joint', Settings())
        for a, b in zip(first['decisions'][:5], second['decisions'][:5]):
            self.assertEqual(a['view'], b['view'])
            self.assertEqual(a['selected'], b['selected'])

    def test_training_and_heldout_seeds_are_disjoint_and_reproducible(self):
        self.assertFalse(set(TRAIN_SEEDS) & set(TEST_SEEDS))
        self.assertEqual(generate('bursty', 101), generate('bursty', 101))
        self.assertNotEqual(generate('bursty', 101).packets, generate('bursty', 211).packets)

    def test_static_does_not_poll_while_online_reads_are_charged(self):
        workload = generate('stable', 101)
        trained = (200000, 50000, 200000, 50000)
        static, raw = simulate(workload, budget_for('stable', 'high'), trained, 'static', Settings())
        self.assertEqual(static['update_rpc_count'], 0)
        self.assertTrue(all(not d['reads'] for d in raw['decisions']))
        _, online = simulate(workload, budget_for('stable', 'high'), trained, 'feedback', Settings())
        self.assertEqual(len(online['decisions'][0]['reads']), 2)
        self.assertEqual(online['summary']['cost']['control_serialized_bytes_by_kind']['observation'], 8 * 242)

    def test_rpc_serialization_matches_archived_actual_writes(self):
        fees = calibration(ROOT)['calls']
        self.assertEqual((fees['threshold_us']['request_bytes'], fees['threshold_us']['reply_bytes']), (94, 30))
        self.assertEqual((fees['enabled']['request_bytes'], fees['enabled']['reply_bytes']), (89, 30))
        self.assertEqual((fees['read']['request_bytes'], fees['read']['reply_bytes']), (81, 40))


class IndependentCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workload = generate('switch', 101)
        cls.trained = (200000, 50000, 200000, 50000)
        _, raw = simulate(cls.workload, budget_for('switch', 'high'), cls.trained, 'joint', Settings())
        cls.raw = plain(raw)
        cls.manifest = plain({'settings': Settings(), 'topology': {'nodes': NODES, 'routes':
                              [{'flow': f, 'objects': route} for f, route in MANAGEMENT.routes.items()]}})
        cls.fees = calibration(ROOT)['calls']

    def check(self, raw):
        return check_raw(plain(self.workload), raw, plain(budget_for('switch', 'high')),
                         self.trained, self.manifest, self.fees)

    def test_saved_run_is_independently_consistent(self):
        self.assertEqual(self.check(self.raw)['decisions'], 10)

    def test_future_notification_leak_is_detected(self):
        altered = copy.deepcopy(self.raw)
        altered['decisions'][0]['view']['notifications'].append(altered['decisions'][5]['view']['notifications'][-1])
        with self.assertRaisesRegex(ValueError, '需求可见前缀'):
            self.check(altered)

    def test_gate_selection_corruption_is_detected(self):
        altered = copy.deepcopy(self.raw)
        altered['result']['selected']['0'] = [255]
        with self.assertRaisesRegex(ValueError, '门控与容量'):
            self.check(altered)

    def test_fee_corruption_is_detected(self):
        altered = copy.deepcopy(self.raw)
        altered['result']['cost_events'][0]['bytes'] += 1
        with self.assertRaisesRegex(ValueError, '费用账本'):
            self.check(altered)

    def test_loss_checker_handles_unknown_and_clipped_boundaries(self):
        task = plain(generate('stable', 11).tasks[0])
        task.update(start_us=0, end_us=10, target_us=1, weight=2)
        value, duration = clipped_loss([task], [], 4)
        self.assertEqual((value, duration), (4, 20))
        receipts = [{'object_id': task['object_id'], 'received_us': 0, 'generated_us': 0}]
        self.assertEqual(clipped_loss([task], receipts, 4)[0], 3.2)


if __name__ == '__main__':
    unittest.main()
