"""报告与决策节拍分离、实际前缀以及身份/尾段边界。"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
from collector.observer_schedule import ObserverSchedule
from collector.available_prefix import ingest_prefix
from collector.into_state import IntoState
from check_report_observer import replay
from report_observer_contract import METHOD_PATH, order, validate
from test_into_live import row, ROUTES, B
from run_into_validation import plain


def fixture(input_rows=None, partial=b''):
    values = [row()] if input_rows is None else input_rows
    raw = b''.join(json.dumps(r).encode() + b'\n' for r in values) + partial
    observations, decisions = [], []
    state = IntoState(ROUTES, 0)
    with tempfile.TemporaryDirectory() as name:
        path = Path(name) / 'collector.jsonl'
        for index in range(10):
            path.write_bytes(raw if index else b'')
            start = index * 100000000 + 100
            clock = iter((start + 1, start + 2, start + 3, start + 4))
            event = ingest_prefix(state, path, lambda: next(clock))
            event.update(observation_index=index, scheduled_offset_us=index * 100000)
            observations.append({'observation_index': index, 'scheduled_offset_us': index * 100000,
                'scheduled_ns': index * 100000000, 'cycle_started_ns': start, 'finished_ns': start + 5,
                'skipped': False, 'decision_due': index % 2 == 0, 'decision_skipped': False, 'lateness_ns': 100,
                'availability': event, 'rpc_start_index': 17, 'rpc_stop_index': 17,
                'fresh_reports': plain(state.reports[event['reports_before']:]),
                'cache_after': plain([state.cache[k] for k in sorted(state.cache)])})
            if index % 2 == 0:
                decisions.append({'scheduled_offset_us': index * 100000, 'cutoff_ns': start + 10,
                                  'cycle_finished_ns': start + 11, 'observation_index': index,
                                  'view': plain(state.snapshot(start + 10, []))})
    return raw, observations, decisions


def check(values): return replay(*values, 0, 100000, 1000000, 200000)


class ObserverTests(unittest.TestCase):
    def test_two_frequencies_have_exact_denominators(self):
        s = ObserverSchedule(4000)
        slots = [s.slot(i, 4000 + i * 100000000) for i in range(s.slots)]
        self.assertEqual(len(slots), 100)
        self.assertEqual([r['observation_index'] for r in slots if r['decision_due']], list(range(0, 100, 10)))
        self.assertEqual(slots[-1]['scheduled_offset_us'], 9900000)

    def test_observation_late_does_not_cancel_valid_decision(self):
        slot = ObserverSchedule(0).slot(0, 100000000)
        self.assertTrue(slot['observation_skipped'])
        self.assertFalse(slot['decision_skipped'])

    def test_exact_decision_deadline_is_skipped(self):
        self.assertTrue(ObserverSchedule(0).slot(0, 1000000000)['decision_skipped'])

    def test_invalid_periods_rejected(self):
        for params in ({'observation_us': 0}, {'decision_us': 100001}, {'duration_us': 10000001},
                       {'origin_ns': -1}, {'origin_ns': True}, {'duration_us': 1.5}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                ObserverSchedule(**dict({'origin_ns': 0}, **params))

    def test_early_and_outside_slot_rejected(self):
        for index, now in ((-1, 0), (100, 10000000000), (1, 99999999)):
            with self.subTest(index=index), self.assertRaises(ValueError):
                ObserverSchedule(0).slot(index, now)

    def test_actual_prefix_and_cache_mapping(self):
        raw, events, decisions = fixture()
        result = check((raw, events, decisions))
        self.assertEqual(result['visible_reports'], 1)
        self.assertEqual(result['shared_cache_advances'], 1)
        self.assertEqual(events[1]['fresh_reports'][0]['records'][0]['generated_local_us'], 50)
        self.assertEqual(decisions[0]['view']['cache'], [])
        self.assertEqual(decisions[1]['view']['cache'][0]['received_us'], 1000)

    def test_duplicate_and_old_reports_do_not_reset(self):
        data = fixture([row(), row(packet_id=900), row(generated=49)])
        result = check(data)
        self.assertEqual(result['visible_reports'], 1)
        self.assertEqual(result['shared_cache_advances'], 1)

    def test_cross_flow_old_record_does_not_reset_shared_cache(self):
        data = fixture([row(node=3, hop=2, generated=80), row(flow=B, node=3, hop=2, generated=79)])
        result = check(data)
        self.assertEqual(result['visible_reports'], 2)
        self.assertEqual(result['shared_cache_advances'], 1)
        self.assertEqual(data[1][-1]['cache_after'][0]['generated_local_us'], 80)

    def test_unknown_session_wrong_route_and_bad_checksum(self):
        bad = row(); bad['session'] = 19
        checksum = row(); checksum['ip_checksum_ok'] = False
        self.assertEqual(check(fixture([bad, row(node=3, hop=1), checksum]))['visible_reports'], 0)

    def test_serial_wrap_advances_without_future_truth(self):
        result = check(fixture([row(generated=2**48 - 2), row(generated=1)]))
        self.assertEqual(result['shared_cache_advances'], 2)

    def test_partial_line_never_enters_state(self):
        data = fixture(partial=json.dumps(row(generated=90)).encode()[:60])
        self.assertEqual(check(data)['visible_reports'], 1)
        self.assertLess(data[1][-1]['availability']['prefix_bytes'], len(data[0]))

    def test_archival_tail_is_not_online_observation(self):
        raw, events, decisions = fixture()
        extra = row(generated=90); extra['receive_monotonic_ns'] = 999000000
        result = check((raw + json.dumps(extra).encode() + b'\n', events, decisions))
        self.assertEqual(result['visible_reports'], 1)

    def test_forged_digest_rejected(self):
        data = fixture(); data[1][1]['availability']['prefix_sha256'] = '0' * 64
        with self.assertRaises(ValueError): check(data)

    def test_future_batch_reference_rejected(self):
        data = fixture(); data[2][0]['observation_index'] = 1
        with self.assertRaises(ValueError): check(data)

    def test_future_receiver_record_rejected(self):
        extra = row(); extra['receive_monotonic_ns'] = 200000000
        with self.assertRaises(ValueError): fixture([extra])

    def test_hidden_tick_rpc_rejected(self):
        data = fixture(); data[1][1]['rpc_stop_index'] += 1
        with self.assertRaises(ValueError): check(data)

    def test_skipped_observation_cannot_invent_data(self):
        data = fixture(); data[1][1]['skipped'] = True
        with self.assertRaises(ValueError): check(data)

    def test_post_cutoff_cache_and_missing_slot_rejected(self):
        data = fixture(); data[2][0]['view']['cache'] = copy.deepcopy(data[1][1]['cache_after'])
        with self.assertRaises(ValueError): check(data)
        data = fixture(); data[1].pop()
        with self.assertRaises(ValueError): check(data)

    def test_frozen_common_methods_share_initialization(self):
        contract = json.loads(METHOD_PATH.read_text())
        entries = order(contract)
        self.assertEqual(len(entries), 8)
        self.assertEqual({e['seed'] for e in entries}, {11})
        for scenario in {e['scenario'] for e in entries}:
            pair = [e for e in entries if e['scenario'] == scenario]
            self.assertEqual(pair[0]['initial_config'], pair[1]['initial_config'])
            self.assertEqual({e['method'] for e in pair}, {'static', 'phase_guard'})
        self.assertEqual({e['parameter_index'] for e in entries if e['method'] == 'phase_guard'}, {2})

    def test_changed_observation_contract_rejected(self):
        with self.assertRaisesRegex(ValueError, '合同'):
            validate({'observation_period_us': 10000})
