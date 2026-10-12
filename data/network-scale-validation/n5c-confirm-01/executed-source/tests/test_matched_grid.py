"""共同配置与自有控制费用的因果、窗口及债务边界。"""
from dataclasses import replace
import copy
from itertools import product
from pathlib import Path
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'src'), str(Path(__file__).resolve().parents[1] / 'experiments')]
from orchestration.adaptive import AdaptiveSettings
from orchestration.control_ledger import API, NOTICE, Charge, ControlLedger, ControlSnapshot, notice_payload
from orchestration.matched_grid import ControlCaps, GRID_US, MatchedProxy, settings, validate_config
from orchestration.rolling import Budget, configurations
from rolling_workloads import MANAGEMENT, budget_for
from test_adaptive import view_at
from check_matched_preflight import check_case_ledger


def rpc(before=1900000000, after=2100000000, request=81, reply=40, success=True, kind='online_observation'):
    return dict(before_monotonic_ns=before, after_monotonic_ns=after, request_serialized_bytes=request,
                reply_serialized_bytes=reply, success=success, kind=kind)


def proxy(ledger=None, history=(), now=3000000, notice_cap=128, api_cap=1200):
    ledger = ControlLedger() if ledger is None else ledger
    view = view_at(now)
    return MatchedProxy(view, MANAGEMENT, replace(budget_for('stable', 'high'), control_bytes_per_s=api_cap),
                        settings(), AdaptiveSettings(), ledger.snapshot(now * 1000), ControlCaps(api_cap, notice_cap), history)


class LedgerTests(unittest.TestCase):
    def test_complete_length_not_exposed_before_return(self):
        ledger = ControlLedger()
        ledger.completed_rpc('read', rpc())
        self.assertFalse(ledger.snapshot(2000000000).charges)
        self.assertEqual(ledger.snapshot(2100000000).window(0, 3000000000)['bytes_by_layer'][API], 121)

    def test_request_and_reply_cross_measurement_boundary(self):
        ledger = ControlLedger()
        ledger.completed_rpc('read', rpc())
        usage = ledger.snapshot(3000000000).window(2000000000, 10000000000)
        self.assertEqual(usage['bytes_by_layer'][API], 40)

    def test_reply_at_right_boundary_excluded(self):
        ledger = ControlLedger()
        ledger.completed_rpc('read', rpc(after=10000000000))
        self.assertEqual(ledger.snapshot(10000000000).window(0, 10000000000)['bytes_by_layer'][API], 81)

    def test_failed_call_and_reconciliation_remain_paid(self):
        ledger = ControlLedger()
        ledger.completed_rpc('bad', rpc(request=97, reply=41, success=False, kind='update'))
        ledger.completed_rpc('reconcile', rpc(before=2200000000, after=2300000000, request=77, kind='reconciliation'))
        self.assertEqual(ledger.snapshot(3000000000).window(0, 3000000000)['bytes_by_layer'][API], 255)

    def test_zero_reply_still_counts_failed_request(self):
        ledger = ControlLedger()
        ledger.completed_rpc('timeout', rpc(reply=0, success=False))
        self.assertEqual(ledger.snapshot(3000000000).window(0, 3000000000)['bytes_by_layer'][API], 81)

    def test_idempotent_reimport_and_conflicting_identity(self):
        ledger = ControlLedger()
        ledger.completed_rpc('same', rpc())
        ledger.completed_rpc('same', rpc())
        self.assertEqual(len(ledger.snapshot(3000000000).charges), 2)
        with self.assertRaises(ValueError):
            ledger.completed_rpc('same', rpc(reply=41))

    def test_notice_uses_received_bytes_without_rpc_layer(self):
        ledger = ControlLedger()
        message = {'version': 1, 'demands': [{'node': 1, 'target_us': 150000, 'weight': 1, 'active': True}]}
        body = notice_payload(message)
        ledger.received_notice('notice', body, 2400000000)
        usage = ledger.snapshot(3000000000).window(0, 3000000000)['bytes_by_layer']
        self.assertEqual(usage, {API: 0, NOTICE: len(body)})
        self.assertNotEqual(len(body), len(notice_payload({**message, 'version': 10000})))

    def test_notice_available_at_receive_not_future_schedule(self):
        ledger = ControlLedger()
        ledger.received_notice('notice', b'abc', 3000000001)
        self.assertFalse(ledger.snapshot(3000000000).charges)

    def test_future_or_duplicate_snapshot_rejected(self):
        charge = Charge('a', API, 'read', 'request', 10, 20, 80, True, 'fixture')
        for entries, now in (((charge,), 19), ((charge, charge), 20)):
            with self.assertRaises(ValueError):
                ControlSnapshot(now, entries)

    def test_bad_time_layer_and_nan_payload_rejected(self):
        ledger = ControlLedger()
        with self.assertRaises(ValueError):
            ledger.completed_rpc('bad', rpc(before=30, after=20))
        with self.assertRaises(ValueError):
            Charge('a', 'frame', 'read', 'request', 10, 20, 80, True, 'fixture')
        with self.assertRaises(ValueError):
            notice_payload({'weight': float('nan')})

    def test_relative_clock_keeps_negative_warmup_cost(self):
        ledger = ControlLedger()
        ledger.completed_rpc('install', rpc(before=10, after=20, kind='installation'), origin_ns=30)
        usage = ledger.snapshot(0)
        self.assertEqual(usage.window(0, 10)['bytes_by_layer'][API], 0)
        self.assertEqual(usage.window(-30, 0)['bytes_by_layer'][API], 121)


class MatchedGridTests(unittest.TestCase):
    def test_all_methods_share_full_configuration_set(self):
        actual = tuple(configurations(MANAGEMENT, settings()))
        self.assertEqual(set(actual), set(product((None,) + GRID_US, repeat=4)))
        self.assertEqual(len(actual), 6561)
        self.assertTrue(all(validate_config(c, MANAGEMENT) == c for c in actual))

    def test_bad_or_legacy_grid_rejected(self):
        with self.assertRaises(ValueError):
            validate_config((False, None, None, None), MANAGEMENT)
        with self.assertRaises(ValueError):
            proxy().evaluate((50001,) * 4)
        with self.assertRaises(ValueError):
            MatchedProxy(view_at(3000000), MANAGEMENT, budget_for('stable', 'high'),
                         replace(settings(), grid_us=(50000, 200000, 1000000)), AdaptiveSettings(),
                         ControlLedger().snapshot(3000000000), ControlCaps(1200, 128))

    def test_control_history_predictions_do_not_replace_actual_spend(self):
        prior = {'time_us': 2000000, 'cost': {'inband_per_link_bytes': {}, 'export_attempt_bytes': 0,
                 'control_serialized_bytes_by_kind': {'observation': 999999}}, 'header_bytes': {}}
        first, second = proxy(), proxy(history=(prior,))
        self.assertEqual(first.effective_budget.control_bytes_per_s, second.effective_budget.control_bytes_per_s)
        self.assertEqual(first.evaluate(first.current)['cost'], second.evaluate(second.current)['cost'])

    def test_actual_debt_reduces_remaining_api_without_merging_notice(self):
        ledger = ControlLedger()
        ledger.completed_rpc('expensive', rpc(before=2200000000, after=2300000000, request=10000))
        p = proxy(ledger)
        self.assertEqual(p.effective_budget.control_bytes_per_s, 0)
        self.assertEqual(p.budget_diagnostic['spend']['control_bytes'], 10040)
        self.assertEqual(p.budget_diagnostic['spend']['notice_payload_bytes'], 0)

    def test_received_notice_not_charged_again_in_candidate(self):
        ledger = ControlLedger()
        ledger.received_notice('notice', b'x' * 192, 3000000000)
        p = proxy(ledger)
        view = replace(p.view, notifications=(replace(p.view.notifications[0], received_us=3000000),))
        p = MatchedProxy(view, MANAGEMENT, p.budget, settings(), AdaptiveSettings(), ledger.snapshot(3000000000), ControlCaps(1200, 128))
        row = p.evaluate(p.current)
        self.assertEqual(row['cost']['control_serialized_bytes_by_kind']['observation'], 242)
        self.assertEqual(p.budget_diagnostic['spend']['notice_payload_bytes'], 192)

    def test_separate_notice_cap_preserves_known_failure(self):
        ledger = ControlLedger()
        ledger.received_notice('notice', b'x' * 100, 2300000000)
        p = proxy(ledger, notice_cap=1)
        self.assertGreater(p.notice_debt, 0)
        self.assertEqual(p.budget_diagnostic['spend']['control_bytes'], 0)
        self.assertGreaterEqual(p.evaluate(p.current)['effective_budget_excess'], p.notice_debt)

    def test_counter_costs_follow_forecast_horizon(self):
        p = proxy()
        doubled = MatchedProxy(p.view, MANAGEMENT, p.budget, replace(settings(), horizon_us=2000000),
                               AdaptiveSettings(), p.control, p.control_caps)
        self.assertEqual(doubled.evaluate(doubled.current)['predicted_api_bytes_per_s'], 121)

    def test_fee_prefix_must_match_view_cutoff_and_cap(self):
        for snapshot, caps in ((ControlLedger().snapshot(2999999000), ControlCaps(1200, 128)),
                               (ControlLedger().snapshot(3000000000), ControlCaps(1000, 128))):
            with self.assertRaises(ValueError):
                MatchedProxy(view_at(3000000), MANAGEMENT, budget_for('stable', 'high'), settings(),
                             AdaptiveSettings(), snapshot, caps)

    def test_future_completed_suffix_cannot_change_selection_inputs(self):
        ledger = ControlLedger()
        before = proxy(ledger)
        ledger.completed_rpc('future', rpc(before=3100000000, after=3200000000, request=99999))
        after = proxy(ledger)
        self.assertEqual(before.budget_diagnostic, after.budget_diagnostic)
        self.assertEqual(before.packets, after.packets)
        self.assertEqual(before.evaluate(before.current), after.evaluate(after.current))


class LedgerCheckerTests(unittest.TestCase):
    def fixture(self):
        operation = dict(rpc(before=2100000000, after=2200000000), method='bm_register_read')
        charges = [dict(operation_id='rpc-0', layer=API, kind='online_observation', part=part,
                        time_ns=time, known_ns=2200000000, bytes=size, success=True)
                   for part, time, size in (('request', 2100000000, 81), ('reply', 2200000000, 40))]
        usage = dict(window_ns=[2000000000, 3000000000], bytes_by_layer={API: 121, NOTICE: 0},
                     bytes_by_layer_and_kind={API: {'online_observation': 121}, NOTICE: {}})
        entry = dict(origin_ns=0, charges=charges, prefixes=[], measurement_window_ns=[2000000000, 3000000000],
                     final_cutoff_ns=3000000000, final_usage=usage)
        return entry, [operation]

    def test_valid_direct_reference(self):
        entry, operations = self.fixture()
        self.assertEqual(check_case_ledger(entry, operations, {}), 3)

    def test_corrupted_actual_bytes_rejected(self):
        entry, operations = self.fixture()
        entry['charges'][0]['bytes'] = 80
        with self.assertRaises(ValueError):
            check_case_ledger(entry, operations, {})

    def test_bulk_diagnostics_do_not_enter_legal_expense_prefix(self):
        entry, operations = self.fixture()
        operations.append(dict(operations[0], method='bm_register_read_all', kind='installation', reply_serialized_bytes=999999))
        self.assertEqual(check_case_ledger(entry, operations, {}), 3)

    def test_diagnostic_reset_not_confused_with_normal_gate_reset(self):
        entry, operations = self.fixture()
        operations.append(dict(operations[0], method='bm_register_reset', kind='cold_start_reset',
                               arguments=['TTEgress.trace_arrival'], reply_serialized_bytes=999999))
        self.assertEqual(check_case_ledger(entry, operations, {}), 3)

    def test_future_response_length_and_duplicate_identity_rejected(self):
        entry, operations = self.fixture()
        for damaged in (dict(entry['charges'][1], known_ns=2100000000), copy.deepcopy(entry['charges'][0])):
            altered = copy.deepcopy(entry)
            if damaged['part'] == 'reply':
                altered['charges'][1] = damaged
            else:
                altered['charges'].append(damaged)
            with self.assertRaises(ValueError):
                check_case_ledger(altered, operations, {})


if __name__ == '__main__':
    unittest.main()
