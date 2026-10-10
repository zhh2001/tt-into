"""真实接口适配的身份、旧报告及逐字段不确定性边界。"""
import sys
from pathlib import Path
import struct
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from collector.codec import checksum
from collector.tagged_codec import WireRecord, encode
from collector.tagged_frame import decode_frame
from collector.into_state import IntoState
from control.into import ConfirmedFields, IntoClient
from orchestration.event_schema import FlowId, ObjectId

A, B = FlowId(20, 1001), FlowId(20, 2002)
ROUTES = {A: (ObjectId(1), ObjectId(3)), B: (ObjectId(2), ObjectId(3))}


def frame(flow=A, node=1, hop=1, generated=50, packet_id=7):
    payload = encode(flow, (WireRecord(node, hop, generated, 4),), 64)
    ip = struct.pack('!BBHHHBBHII', 0x45, 0, 20 + len(payload), packet_id, 0, 61, 17, 0, 0x0a000001, 0x0a000002)
    ip = ip[:10] + checksum(ip).to_bytes(2, 'big') + ip[12:]
    return b'\0' * 12 + b'\x88\xb6' + ip + payload


def row(**kwargs):
    value = decode_frame(frame(**kwargs))
    value['receive_monotonic_ns'] = 1000000
    return value


class FakeClient:
    def __init__(self):
        self.log, self.registers = [], {}

    def write(self, name, index, value, kind):
        failure = 'missing' in name
        self.log.append({'after_monotonic_ns': 0, 'success': not failure})
        if failure:
            raise RuntimeError('受控请求失败')
        self.registers[name, index] = value

    def read(self, name, index, kind):
        return self.registers.get((name, index), 0)


class LiveIdentityTests(unittest.TestCase):
    def test_wire_identity_is_independent_of_ip_id(self):
        for pid in (1, 60000):
            value = decode_frame(frame(packet_id=pid))
            self.assertEqual((value['flow_tag'], value['session']), (1001, 20))
            self.assertEqual(value['ip_length'], 44)
            self.assertTrue(value['ip_checksum_ok'])

    def test_truncated_and_wrong_version(self):
        with self.assertRaises(ValueError):
            decode_frame(frame()[:-1])
        value = bytearray(frame())
        value[36] = 5
        with self.assertRaises(ValueError):
            decode_frame(value)

    def test_future_frame_not_visible(self):
        state = IntoState(ROUTES, 0)
        state.ingest([row()], 999999)
        self.assertFalse(state.reports)
        state.ingest([row()], 1000000)
        self.assertEqual(len(state.reports), 1)

    def test_old_session_and_wrong_hop_rejected(self):
        state = IntoState(ROUTES, 0)
        state.ingest([row(flow=FlowId(19, 1001)), row(node=3, hop=1)], 1000000)
        self.assertFalse(state.cache)
        self.assertFalse(state.reports)

    def test_duplicate_and_old_do_not_refresh_cache(self):
        state = IntoState(ROUTES, 0)
        state.ingest([row(generated=50)], 1000000)
        old = row(generated=49)
        old['receive_monotonic_ns'] = 2000000
        state.ingest([row(generated=50), old], 2000000)
        self.assertEqual(state.cache[ObjectId(1)].received_us, 1000)
        self.assertEqual(len(state.reports), 1)

    def test_same_node_shared_across_flows(self):
        state = IntoState(ROUTES, 0)
        state.ingest([row(node=3, hop=2, generated=80), row(flow=B, node=3, hop=2, generated=79)], 1000000)
        self.assertEqual(state.cache[ObjectId(3)].generated_local_us, 80)

    def test_serial_wrap_advances(self):
        state = IntoState(ROUTES, 0)
        state.ingest([row(generated=2**48 - 2), row(generated=1)], 1000000)
        self.assertEqual(state.cache[ObjectId(1)].generated_local_us, 1)

    def test_notice_version_and_active_semantics(self):
        state = IntoState(ROUTES, 0)
        message = {'version': 1, 'demands': [{'node': 1, 'target_us': 1000, 'weight': 1, 'active': False}]}
        state.notify(message, 1000000)
        self.assertFalse(state.snapshot(1000000, []).notifications[0].demands[0].active)
        with self.assertRaises(ValueError):
            state.notify(message, 2000000)

    def test_delayed_confirmation_blocks_field(self):
        client = FakeClient()
        state = ConfirmedFields({1: client}, {A: 1}, 0)
        with patch('control.into.time.monotonic_ns', return_value=50):
            state.write(1, A, 'threshold_us', 50000, delay_ns=100)
        self.assertTrue(state.unresolved())
        self.assertFalse(state.confirmations)
        state.poll(100)
        self.assertFalse(state.unresolved())
        self.assertEqual(state.values[1, A, 'threshold_us'], 50000)

    def test_failed_enable_after_successful_threshold(self):
        client = FakeClient()
        state = ConfirmedFields({1: client}, {A: 1}, 0)
        state.write(1, A, 'threshold_us', 50000)
        state.write(1, A, 'enabled', True, bad_register=True)
        self.assertEqual(state.values[1, A, 'threshold_us'], 50000)
        self.assertIn((1, A, 'enabled'), state.unresolved())
        self.assertNotIn((1, A, 'enabled'), state.values)
        state.reconcile()
        self.assertFalse(state.values[1, A, 'enabled'])
        self.assertFalse(state.unresolved())

    def test_second_pending_write_rejected(self):
        client = FakeClient()
        state = ConfirmedFields({1: client}, {A: 1}, 0)
        with patch('control.into.time.monotonic_ns', return_value=10):
            state.write(1, A, 'enabled', False, delay_ns=100)
            with self.assertRaises(ValueError):
                state.write(1, A, 'enabled', True)

    def test_rpc_exception_is_metered(self):
        client = object.__new__(IntoClient)
        class Counter:
            sent = 0
            received = 0
        client.socket, client.log, client.node = Counter(), [], 1
        class Api:
            def bad(self, context, value):
                client.socket.sent += 80
                client.socket.received += 24
                raise RuntimeError('受控错误')
        client.api = Api()
        with self.assertRaises(RuntimeError):
            client.call('bad', 7, kind='update')
        self.assertFalse(client.log[0]['success'])
        self.assertEqual((client.log[0]['request_serialized_bytes'], client.log[0]['reply_serialized_bytes']), (80, 24))


if __name__ == '__main__':
    unittest.main()
