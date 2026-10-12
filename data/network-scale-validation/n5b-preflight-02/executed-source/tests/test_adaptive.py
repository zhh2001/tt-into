"""机会观察偏差、动作边界及预算估计的独立输入检查。"""
from dataclasses import replace
from pathlib import Path
import copy
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'experiments'))
from collector.tagged_codec import WireRecord
from orchestration.adaptive import (AdaptiveProxy, AdaptiveSettings, forecast_packets, latest_rates, post_notice_counts_ready,
                                    profile, remaining_budget, select, spend_estimate, unique_reports)
from orchestration.event_observation import Confirmation, CountObservation, DemandNotification, OnlineView, ReceivedReport
from orchestration.rolling import Settings, installation
from adaptive_workloads import CALIBRATION_SEEDS, SCENARIOS, TEST_SEEDS, TRAIN_SEEDS, generate
from rolling_workloads import FLOWS, MANAGEMENT, NODES, budget_for
from run_adaptive_reference import adaptive_simulate, calibration_score
from run_rolling_reference import plain
from check_rolling_reference import check_raw
from check_adaptive_reference import check_adaptive_raw


def view_at(now=2000000, times=(), rates=(8, 8)):
    config = (50000,) * 4
    confirmations = tuple(Confirmation(w.write_id, w.ack_us, w.node, w.flow, w.field, w.value, w.version, True)
                          for w in installation(config, MANAGEMENT, Settings()))
    counts = tuple(CountObservation(f'{i}-{f.tag}', f, i * 1000000 + 1, i * 1000000 + 101, i * rate)
                   for i in range(max(2, now // 1000000)) for f, rate in zip(FLOWS, rates))
    reports = tuple(ReceivedReport(t + 1030, f, (WireRecord(MANAGEMENT.routes[f][0].node, 1, t + 5, 0),))
                    for t in times for f in FLOWS if t + 1030 <= now)
    return OnlineView(now, reports, counts, confirmations, (),
                      (DemandNotification(0, 1, generate('stable', 11).notices[0].demands),))


class OpportunityTests(unittest.TestCase):
    def test_recent_difference_avoids_extra_ewma_lag(self):
        view = view_at(3000000)
        counts = tuple(replace(r, count=22 if r.flow == FLOWS[0] else 10) if r.issued_us > 2000000 else r for r in view.counts)
        rates = latest_rates(replace(view, counts=counts))
        self.assertEqual((rates[FLOWS[0]], rates[FLOWS[1]]), (14, 2))

    def test_counter_wrap_zero_rate_and_reset(self):
        view = view_at()
        first = replace(view.counts[0], count=2**32 - 2)
        second = replace(view.counts[2], count=3)
        rates = latest_rates(replace(view, counts=(first, second)))
        self.assertEqual(rates[first.flow], 5)
        self.assertEqual(latest_rates(replace(view, now_us=3000000, counts=(second, replace(second, available_us=second.available_us + 1000000))))[first.flow], 0)
        self.assertIsNone(latest_rates(replace(view, now_us=3000000, counts=(second, replace(second, available_us=second.available_us + 1000000, count=2))))[first.flow])

    def test_future_count_and_reversed_responses_rejected(self):
        view = view_at()
        with self.assertRaises(ValueError):
            latest_rates(replace(view, counts=(replace(view.counts[0], available_us=view.now_us + 1),)))
        with self.assertRaises(ValueError):
            latest_rates(replace(view, counts=(view.counts[2], view.counts[0])))

    def test_cluster_uses_report_spacing_and_paid_count_for_hidden_positions(self):
        times = [100000 + i * 500000 + j * 1000 for i in range(4) for j in range(2)]
        view = view_at(times=times)
        p = profile(view, FLOWS[0], MANAGEMENT.routes[FLOWS[0]], 8, Settings(), AdaptiveSettings())
        self.assertEqual(p['mode'], 'periodic_cluster')
        self.assertEqual((p['period_us'], p['burst_packets'], p['inner_spacing_us']), (500000, 4, 1000))
        packets = forecast_packets((p,), view, MANAGEMENT, Settings())
        self.assertEqual([p.hops[0].ingress_us for p in packets], [2100000, 2101000, 2102000, 2103000, 2600000, 2601000, 2602000, 2603000])

    def test_insufficient_cluster_evidence_does_not_claim_burst(self):
        view = view_at(times=(100000, 101000, 600000, 601000))
        p = profile(view, FLOWS[0], MANAGEMENT.routes[FLOWS[0]], 8, Settings(), AdaptiveSettings())
        self.assertEqual(p['mode'], 'count_period_with_report_phase')

    def test_sampled_period_not_automatically_a_source_period(self):
        view = view_at(times=(100000, 600000, 1100000, 1600000))
        p = profile(view, FLOWS[0], MANAGEMENT.routes[FLOWS[0]], 8, Settings(), AdaptiveSettings())
        self.assertEqual(p['period_us'], 125000)
        self.assertEqual(p['mode'], 'count_period_with_report_phase')

    def test_duplicate_and_late_old_report_do_not_add_opportunities(self):
        view = view_at(times=(100000, 101000, 600000, 601000, 1100000, 1101000))
        duplicate = replace(view.reports[0], received_us=1500000)
        old = replace(duplicate, records=(replace(duplicate.records[0], generated_local_us=1),))
        modified = replace(view, reports=view.reports + (duplicate, old))
        self.assertEqual(unique_reports(modified), unique_reports(view))
        a = AdaptiveProxy(view, MANAGEMENT, budget_for('stable', 'high'), Settings(), AdaptiveSettings())
        b = AdaptiveProxy(modified, MANAGEMENT, budget_for('stable', 'high'), Settings(), AdaptiveSettings())
        self.assertEqual(a.packets, b.packets)

    def test_zero_count_supply_does_not_continue_old_report_pattern(self):
        view = view_at(times=(100000, 600000, 1100000))
        counts = tuple(replace(r, count=0) for r in view.counts)
        proxy = AdaptiveProxy(replace(view, counts=counts), MANAGEMENT, budget_for('stable', 'low'), Settings(), AdaptiveSettings())
        self.assertEqual(proxy.packets, ())

    def test_no_reports_falls_back_to_declared_uniform_phase(self):
        proxy = AdaptiveProxy(view_at(), MANAGEMENT, budget_for('stable', 'low'), Settings(), AdaptiveSettings())
        self.assertTrue(all(p['mode'] == 'uniform_prior' for p in proxy.profiles))
        self.assertEqual(len(proxy.packets), 16)

    def test_forecast_event_limit_is_enforced_for_extreme_estimate(self):
        p = {'flow': FLOWS[0], 'period_us': 1, 'anchor_us': 0, 'burst_packets': 1}
        packets = forecast_packets((p,), view_at(), MANAGEMENT, Settings())
        self.assertEqual(len(packets), 100)


class GuardTests(unittest.TestCase):
    def history(self, inband=0, exported=0, controlled=242):
        cost = {'inband_per_link_bytes': {'e1': inband, 'e2': inband, 'e3': inband},
                'export_attempt_bytes': exported, 'control_serialized_bytes_by_kind': {'observation': controlled}}
        return [{'time_us': 2000000, 'cost': cost, 'header_bytes': {'e1': 0, 'e2': 0, 'e3': 0}}]

    def test_unused_budget_credit_and_debt_change_remaining_rates(self):
        view = view_at(3000000)
        budget = budget_for('stable', 'low')
        credit, _ = remaining_budget(view, MANAGEMENT, budget, dict(zip(FLOWS, (0, 0))), Settings(), AdaptiveSettings(), self.history())
        debt, diag = remaining_budget(view, MANAGEMENT, budget, dict(zip(FLOWS, (0, 0))), Settings(), AdaptiveSettings(), self.history(2000, 5000))
        self.assertGreater(credit.export_bytes_per_s, budget.export_bytes_per_s)
        self.assertEqual(debt.export_bytes_per_s, 0)
        self.assertEqual(debt.link_bytes_per_s['e1'], 0)
        self.assertGreater(diag['spend']['export_bytes'], 8 * budget.export_bytes_per_s)

    def test_received_reports_remain_a_lower_bound_when_prediction_is_zero(self):
        view = view_at(3000000, times=(2100000,))
        estimates = spend_estimate(view, MANAGEMENT, dict(zip(FLOWS, (8, 8))), Settings(), AdaptiveSettings(), [])
        self.assertEqual(estimates['received_export_lower_bytes'], 120)
        self.assertGreaterEqual(estimates['export_bytes'], 120)
        self.assertGreaterEqual(estimates['inband_bytes']['e3'], estimates['received_metadata_lower_bytes']['e3'])

    def test_missing_reports_do_not_erase_predicted_attempt_fees(self):
        estimates = spend_estimate(view_at(3000000), MANAGEMENT, dict(zip(FLOWS, (8, 8))), Settings(), AdaptiveSettings(), self.history(100, 480))
        self.assertEqual(estimates['export_bytes'], 480)
        self.assertEqual(estimates['received_export_lower_bytes'], 0)

    def test_budget_history_cannot_include_current_or_future_prediction(self):
        for time in (3000000, 4000000):
            history = self.history()
            history[0]['time_us'] = time
            with self.assertRaises(ValueError):
                remaining_budget(view_at(3000000), MANAGEMENT, budget_for('stable', 'low'), {}, Settings(), AdaptiveSettings(), history)

    def test_notification_requires_two_post_notice_returned_reads(self):
        view = view_at()
        self.assertTrue(post_notice_counts_ready(view, MANAGEMENT))
        changed = replace(view, notifications=view.notifications + (replace(view.notifications[0], received_us=1500000, version=2),))
        self.assertFalse(post_notice_counts_ready(changed, MANAGEMENT))

    def test_gain_margin_holds_small_moves_but_budget_repair_can_override(self):
        view = view_at()
        current = {'config': (200000,) * 4, 'predicted_loss': 1.0, 'effective_budget_excess': 0.0}
        proposed = {'config': (50000,) * 4, 'predicted_loss': .97, 'effective_budget_excess': 0.0}
        proxy = SimpleNamespace(view=view, management=MANAGEMENT, current=current['config'], guarded=True,
                                adaptive=AdaptiveSettings(gain_margin=.05), evaluate=lambda _: current)
        candidate = {'config': proposed['config'], 'forecast': proposed, 'evaluated_candidates': 2, 'reason': 'search'}
        with patch('orchestration.adaptive.choose', return_value=copy.deepcopy(candidate)):
            result = select(proxy, current['config'])
        self.assertEqual(result['config'], current['config'])
        current['effective_budget_excess'] = .5
        proposed['predicted_loss'] = 2.0
        with patch('orchestration.adaptive.choose', return_value=copy.deepcopy(candidate)):
            result = select(proxy, current['config'])
        self.assertEqual(result['config'], proposed['config'])
        self.assertTrue(result['budget_repair_action'])

    def test_bad_parameters_rejected(self):
        for values in ({'gain_margin': -1}, {'gain_margin': float('nan')}, {'reserve_fraction': 1}, {'lookback_us': 0}):
            with self.assertRaises(ValueError):
                AdaptiveSettings(**values)


class AdaptiveIntegrationTests(unittest.TestCase):
    def test_seed_sets_disjoint_and_stresses_reproducible(self):
        groups = [set(TRAIN_SEEDS), set(CALIBRATION_SEEDS), set(TEST_SEEDS), {101, 211, 307, 401}]
        self.assertTrue(all(not a & b for i, a in enumerate(groups) for b in groups[i + 1:]))
        self.assertEqual(generate('bursty_jitter', 37), generate('bursty_jitter', 37))
        self.assertNotEqual(generate('bursty_jitter', 37).packets, generate('bursty', 37).packets)
        self.assertEqual(len(generate('unannounced_switch', 37).notices), 1)

    def test_future_suffix_cannot_change_adaptive_decisions(self):
        workload = generate('switch', 37)
        altered = replace(workload, packets=tuple(p for p in workload.packets if p.hops[0].ingress_us < 5000000),
                          notices=workload.notices[:1])
        config = (200000, 1000000, 200000, 50000)
        _, first = adaptive_simulate(workload, budget_for('switch', 'high'), config, 'phase_guard', AdaptiveSettings())
        _, second = adaptive_simulate(altered, budget_for('switch', 'high'), config, 'phase_guard', AdaptiveSettings())
        for a, b in zip(first['decisions'][:5], second['decisions'][:5]):
            self.assertEqual(a['view'], b['view'])
            self.assertEqual(a['selected'], b['selected'])
            self.assertEqual(a['budget_diagnostic'], b['budget_diagnostic'])

    def test_new_methods_obey_same_independent_wire_and_fee_contract(self):
        workload = generate('bursty_jitter', 37)
        trained = (50000,) * 4
        manifest = plain({'settings': Settings(), 'topology': {'nodes': NODES, 'routes':
                         [{'flow': f, 'objects': route} for f, route in MANAGEMENT.routes.items()]}})
        fees = {k: plain(MANAGEMENT.read_cost if k == 'read' else MANAGEMENT.cost_for_write(k))
                for k in ('read', 'enabled', 'threshold_us')}
        for method in ('phase_joint', 'uniform_guard', 'phase_guard', 'phase_local_guard'):
            _, raw = adaptive_simulate(workload, budget_for('bursty_jitter', 'low'), trained, method, AdaptiveSettings(.05, .1))
            counts = check_raw(plain(workload), plain(raw), plain(budget_for('bursty_jitter', 'low')), trained, manifest, fees)
            self.assertEqual(counts['decisions'], 10)

    def test_calibration_prioritizes_actual_feasibility_before_loss(self):
        good = [{'feasible': True, 'excess': 0, 'loss': 1, 'update_rpc_count': 1}]
        bad = [{'feasible': False, 'excess': .01, 'loss': .5, 'update_rpc_count': 0}]
        self.assertLess(calibration_score(good, AdaptiveSettings()), calibration_score(bad, AdaptiveSettings()))

    def test_independent_adaptive_provenance_audit_accepts_valid_trace(self):
        workload = generate('bursty', 37)
        _, raw = adaptive_simulate(workload, budget_for('bursty', 'low'), (50000,) * 4, 'phase_guard', AdaptiveSettings())
        manifest = plain({'settings': Settings(), 'topology': {'nodes': NODES, 'routes':
                         [{'flow': f, 'objects': route} for f, route in MANAGEMENT.routes.items()]}})
        self.assertEqual(check_adaptive_raw(plain(raw), manifest, plain(AdaptiveSettings())), 10)

    def test_future_budget_history_and_fake_anchor_are_rejected(self):
        workload = generate('bursty', 37)
        _, raw = adaptive_simulate(workload, budget_for('bursty', 'low'), (50000,) * 4, 'phase_guard', AdaptiveSettings())
        raw = plain(raw)
        manifest = plain({'settings': Settings(), 'topology': {'nodes': NODES, 'routes':
                         [{'flow': f, 'objects': route} for f, route in MANAGEMENT.routes.items()]}})
        altered = copy.deepcopy(raw)
        altered['decisions'][0]['budget_history'].append(altered['decisions'][3]['budget_history'][0])
        with self.assertRaisesRegex(ValueError, '过去自身'):
            check_adaptive_raw(altered, manifest, plain(AdaptiveSettings()))
        altered = copy.deepcopy(raw)
        altered['decisions'][2]['profiles'][0]['anchor_us'] += 123
        with self.assertRaisesRegex(ValueError, '相位锚点'):
            check_adaptive_raw(altered, manifest, plain(AdaptiveSettings()))


if __name__ == '__main__':
    unittest.main()
