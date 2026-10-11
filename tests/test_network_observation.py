"""观察链的重复、共享缓存、尾段、条件界与来源破坏检查。"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments'))
from observation_diagnostics import advances, bind_input, configuration_at, report_chain, task_integral
from diagnose_network_observation import first_divergence
from check_network_observation import check_contributions, check_index


def frame(stamp, received, tag=1001):
    return {'ether_type': 0x88b6, 'session': 20, 'flow_tag': tag, 'packet_id': received,
            'ip_checksum_ok': True, 'receive_monotonic_ns': received,
            'records': [{'switch_id': 3, 'hop': 2, 'generated_us': stamp, 'queue_packets': 0}]}


def report(row):
    return {'received_us': row['receive_monotonic_ns'] // 1000,
            'flow': {'session': row['session'], 'tag': row['flow_tag']},
            'records': [{'node': 3, 'hop': 2, 'generated_local_us': row['records'][0]['generated_us'], 'queue_packets': 0}]}


def decision(raw, count, before, accepted, cache, cycle=0, at=1000000, partial=0):
    prefix = b''.join(raw.splitlines(keepends=True)[:count])
    e = {'read_started_ns': at, 'read_finished_ns': at + 10, 'ingest_started_ns': at + 20,
         'available_ns': at + 30, 'file_bytes': len(prefix) + partial, 'prefix_bytes': len(prefix),
         'row_count': count, 'prefix_sha256': hashlib.sha256(prefix).hexdigest(),
         'reports_before': before, 'reports_after': len(accepted), 'cutoff_ns': at + 40, 'scheduled_offset_us': cycle}
    return {'cycle_started_ns': at, 'scheduled_offset_us': cycle, 'cutoff_ns': at + 40,
            'solver_started_ns': at + 50, 'availability': e, 'view': {'reports': accepted, 'cache': cache}}


def cache(row):
    return [{'object_id': {'node': 3, 'port': 2, 'queue': 0},
             'generated_local_us': row['records'][0]['generated_us'],
             'received_us': row['receive_monotonic_ns'] // 1000, 'queue_packets': 0}]


class ObservationTests(unittest.TestCase):
    routes = {(20, 1001): (1, 3), (20, 2002): (2, 3)}

    def raw(self, values):
        return b''.join((json.dumps(v) + '\n').encode() for v in values)

    def test_batch_intermediate_is_not_batch_latest(self):
        a, b = frame(10, 100000), frame(20, 200000)
        raw = self.raw([a, b]); d = decision(raw, 2, 0, [report(a), report(b)], cache(b))
        _, chain, _, _ = report_chain(raw, [d], 0, self.routes)
        self.assertTrue(chain[0]['records'][0]['advances_shared_cache'])
        self.assertFalse(chain[0]['records'][0]['batch_latest'])
        self.assertTrue(chain[1]['records'][0]['batch_latest'])

    def test_older_cross_flow_is_new_report_but_does_not_reset_shared_cache(self):
        a, b = frame(20, 100000), frame(10, 200000, 2002)
        raw = self.raw([a, b]); d = decision(raw, 2, 0, [report(a), report(b)], cache(a))
        _, chain, _, _ = report_chain(raw, [d], 0, self.routes)
        self.assertTrue(chain[1]['records'][0]['fresh_per_flow'])
        self.assertFalse(chain[1]['records'][0]['advances_shared_cache'])

    def test_duplicate_and_old_do_not_reset(self):
        a, duplicate, old = frame(20, 100000), frame(20, 200000), frame(10, 300000)
        raw = self.raw([a, duplicate, old]); d = decision(raw, 3, 0, [report(a)], cache(a))
        _, chain, _, _ = report_chain(raw, [d], 0, self.routes)
        self.assertEqual([chain[i]['records'][0]['status'] for i in range(3)], ['fresh', 'duplicate', 'old_record'])

    def test_unread_tail_never_gets_a_fictional_entry(self):
        a, b = frame(20, 100000), frame(30, 200000)
        raw = self.raw([a, b]); d = decision(raw, 1, 0, [report(a)], cache(a))
        _, chain, _, _ = report_chain(raw, [d], 0, self.routes)
        self.assertEqual(chain[1]['records'][0]['status'], 'not_read_in_cycles')
        self.assertIsNone(chain[1]['entered_upper_ns'])

    def test_partial_line_cannot_be_used(self):
        a, b = frame(20, 100000), frame(30, 200000)
        raw = self.raw([a, b]); d = decision(raw, 1, 0, [report(a)], cache(a), partial=12)
        _, chain, _, _ = report_chain(raw, [d], 0, self.routes)
        self.assertIsNone(chain[1]['entered_upper_ns'])

    def test_repeated_prefix_does_not_duplicate_reports(self):
        a = frame(20, 100000); raw = self.raw([a])
        ds = [decision(raw, 1, 0, [report(a)], cache(a)),
              decision(raw, 1, 1, [report(a)], cache(a), 1000000, 2000000)]
        _, chain, batches, _ = report_chain(raw, ds, 0, self.routes)
        self.assertEqual(chain[0]['cycle_offset_us'], 0)
        self.assertEqual(batches[1]['accepted_records'], [])

    def test_unknown_session_not_used(self):
        a = frame(20, 100000); a['session'] = 21; raw = self.raw([a])
        _, chain, _, _ = report_chain(raw, [decision(raw, 1, 0, [], [])], 0, self.routes)
        self.assertEqual(chain[0]['records'][0]['status'], 'invalid_report')

    def test_invalid_route_not_used(self):
        a = frame(20, 100000); a['records'][0]['hop'] = 1; raw = self.raw([a])
        _, chain, _, _ = report_chain(raw, [decision(raw, 1, 0, [], [])], 0, self.routes)
        self.assertFalse(chain[0]['records'][0]['fresh_per_flow'])

    def test_future_receipt_rejected(self):
        a = frame(20, 2000000); raw = self.raw([a]); d = decision(raw, 1, 0, [report(a)], cache(a))
        with self.assertRaises(ValueError): report_chain(raw, [d], 0, self.routes)

    def test_tampered_prefix_rejected(self):
        a = frame(20, 100000); raw = self.raw([a]); d = decision(raw, 1, 0, [report(a)], cache(a))
        d['availability']['prefix_sha256'] = '0' * 64
        with self.assertRaises(ValueError): report_chain(raw, [d], 0, self.routes)

    def test_old_report_cannot_be_forged_into_cache(self):
        a, b = frame(20, 100000), frame(10, 200000)
        raw = self.raw([a, b]); d = decision(raw, 2, 0, [report(a)], cache(b))
        with self.assertRaises(ValueError): report_chain(raw, [d], 0, self.routes)

    def test_modular_forward_and_half_range(self):
        self.assertTrue(advances(2, 2**48 - 3))
        self.assertFalse(advances(5, 5))
        self.assertFalse(advances(4, 5))
        self.assertFalse(advances(2**47, 0))

    def task(self):
        return {'object_id': {'node': 3, 'port': 2, 'queue': 0}, 'start_us': 0, 'end_us': 1000000,
                'target_us': 100000, 'weight': 4}

    def sample(self, gen, at):
        return {'node': 3, 'generation_lower_ns': gen, 'generation_upper_ns': gen, 'received_ns': at}

    def test_clipping_integral_golden_value(self):
        loss, unknown = task_integral(self.task(), [self.sample(0, 0)], 0, 'generation_lower_ns', 'received_ns')
        self.assertAlmostEqual(loss, 3.2); self.assertEqual(unknown, 0)

    def test_unknown_object_remains_in_denominator(self):
        loss, unknown = task_integral(self.task(), [], 0, 'generation_lower_ns', 'received_ns')
        self.assertEqual((loss, unknown), (4, 1000000000))

    def test_pre_task_sample_is_retained(self):
        task = self.task(); task.update(start_us=100000, end_us=200000)
        loss, unknown = task_integral(task, [self.sample(0, 0)], 0, 'generation_lower_ns', 'received_ns')
        self.assertAlmostEqual(loss, 1.5); self.assertEqual(unknown, 0)

    def test_delayed_initial_entry_keeps_unknown_prefix(self):
        loss, unknown = task_integral(self.task(), [self.sample(0, 200000000)], 0, 'generation_lower_ns', 'received_ns')
        self.assertAlmostEqual(loss, 3.8); self.assertEqual(unknown, 200000000)

    def test_late_old_sample_does_not_reset_age(self):
        samples = [self.sample(0, 0), self.sample(500000000, 500000000), self.sample(400000000, 700000000)]
        loss, _ = task_integral(self.task(), samples, 0, 'generation_lower_ns', 'received_ns')
        self.assertAlmostEqual(loss, 2.4)

    def test_future_generation_rejected(self):
        with self.assertRaises(ValueError):
            task_integral(self.task(), [self.sample(2, 1)], 0, 'generation_lower_ns', 'received_ns')

    def test_confirmation_does_not_imply_atomic_effect(self):
        actions = [{'node': 3, 'flow': {'session': 20, 'tag': 1001}, 'field': 'threshold_us', 'value': 100,
                    'success': True, 'confirmation_available_ns': 50, 'rpc_index': 0},
                   {'node': 3, 'flow': {'session': 20, 'tag': 1001}, 'field': 'threshold_us', 'value': 200,
                    'success': True, 'confirmation_available_ns': 150, 'rpc_index': 1}]
        rpc = [{'before_monotonic_ns': 10, 'after_monotonic_ns': 40}, {'before_monotonic_ns': 100, 'after_monotonic_ns': 140}]
        value = configuration_at(actions, rpc, (20, 1001), 3, 110, 120)
        self.assertEqual((value['confirmed_threshold_us'], value['configuration_interval_overlaps'], value['configuration_not_yet_confirmed']), (100, 1, 1))

    def test_failed_field_does_not_change_confirmed_state(self):
        actions = [{'node': 3, 'flow': {'session': 20, 'tag': 1001}, 'field': 'threshold_us', 'value': 100,
                    'success': True, 'confirmation_available_ns': 50, 'rpc_index': 0},
                   {'node': 3, 'flow': {'session': 20, 'tag': 1001}, 'field': 'threshold_us', 'value': 200,
                    'success': False, 'confirmation_available_ns': 150, 'rpc_index': 1}]
        rpc = [{'before_monotonic_ns': 10, 'after_monotonic_ns': 40}, {'before_monotonic_ns': 100, 'after_monotonic_ns': 140}]
        self.assertEqual(configuration_at(actions, rpc, (20, 1001), 3, 200, 210)['confirmed_threshold_us'], 100)

    def test_divergence_ignores_unselected_timing(self):
        a = [{'cycle_offset_us': 0, 'decision': {'config': [100], 'reason': 'hold'}, 'read_ns': 2}]
        b = copy.deepcopy(a); b[0]['read_ns'] = 3
        self.assertIsNone(first_divergence(a, b, 'decision'))

    def test_first_branch_divergence_retained(self):
        a = [{'cycle_offset_us': 0, 'decision': 'hold'}, {'cycle_offset_us': 1000000, 'decision': 'write'}]
        b = copy.deepcopy(a); b[1]['decision'] = 'hold'
        self.assertEqual(first_divergence(a, b, 'decision')['cycle_offset_us'], 1000000)

    def test_missing_repeat_cycle_rejected(self):
        with self.assertRaises(ValueError): first_divergence([{'cycle_offset_us': 0}], [], 'decision')

    def test_different_input_manifest_rejected_before_reading_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp); (p / 'manifest.json').write_text('{}')
            with self.assertRaises(ValueError): bind_input(p)

    def test_failed_budget_cannot_be_omitted_from_index(self):
        with self.assertRaises(ValueError):
            check_index({'budget_failures': []}, [{'name': 'case', 'window_feasibility': 'exceeds'}], [], [], [])

    def test_largest_read_index_cannot_select_smaller_batch(self):
        index = {'budget_failures': [], 'largest_read_batches': [{'read_ms': 1}],
                 'largest_send_lateness': [], 'largest_collector_repeat_ranges': []}
        with self.assertRaises(ValueError): check_index(index, [], [{'read_ms': 1}, {'read_ms': 100}], [], [])

    def contribution_fixture(self):
        tasks = [{'task_id': 'task', 'node': 3, 'denominator_fraction': 1,
                  'collector_loss_lower': .6, 'collector_loss_upper': .6,
                  'controller_loss_lower': 2.9, 'controller_loss_upper': 2.9,
                  'penalty_lower': 2.3, 'penalty_upper': 2.3}]
        static = copy.deepcopy(tasks); static[0].update(collector_loss_lower=.63, collector_loss_upper=.63,
                                                       controller_loss_lower=2.85, controller_loss_upper=2.85,
                                                       penalty_lower=2.22, penalty_upper=2.22)
        pair = {'task_contributions': [{'task_id': 'task', 'node': 3, 'denominator_fraction': 1,
                 'collector_difference_midpoint_contribution': -.03,
                 'controller_difference_midpoint_contribution': .05,
                 'penalty_difference_midpoint_contribution': .08}]}
        return pair, tasks, static

    def test_independent_contribution_matches_golden_values(self):
        check_contributions(*self.contribution_fixture())

    def test_forged_task_contribution_rejected(self):
        pair, tasks, static = self.contribution_fixture()
        pair['task_contributions'][0]['collector_difference_midpoint_contribution'] = -.05
        with self.assertRaises(ValueError): check_contributions(pair, tasks, static)

    def test_forged_contribution_object_rejected(self):
        pair, tasks, static = self.contribution_fixture(); pair['task_contributions'][0]['node'] = 1
        with self.assertRaises(ValueError): check_contributions(pair, tasks, static)


if __name__ == '__main__': unittest.main()
