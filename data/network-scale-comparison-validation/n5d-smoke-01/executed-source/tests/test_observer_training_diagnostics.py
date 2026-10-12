import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
from diagnose_observer_training import business_packet


def packet(identity, body=b'abcdefgh'):
    payload = identity.to_bytes(4, 'big') + body
    frame = bytearray(42) + payload
    frame[12:14] = b'\x08\x00'
    frame[14] = 0x45
    frame[18:20] = identity.to_bytes(2, 'big')
    frame[23] = 17
    frame[24:26] = (identity + 1).to_bytes(2, 'big')
    frame[34:40] = bytes.fromhex('277523280014')
    frame[40:42] = (identity + 2).to_bytes(2, 'big')
    return dict(packet_id=identity, frame_hex=frame.hex(),
                expected_transport_hex=frame[34:].hex(), payload_hex=payload.hex(),
                flow=1, path=[1, 3], offset_ns=100000, ip_length=40,
                registered=True, injected_stale_session=False)


class ObserverTrainingDiagnosticsTests(unittest.TestCase):
    def test_identity_and_checksums_only_are_normalized(self):
        self.assertEqual(business_packet(packet(123)), business_packet(packet(234)))

    def test_application_content_remains_distinct(self):
        self.assertNotEqual(business_packet(packet(123)), business_packet(packet(234, b'abcdefgX')))

    def test_planned_time_route_and_registration_remain_distinct(self):
        for key, value in (('offset_ns', 100001), ('path', [2, 3]), ('registered', False)):
            changed = copy.deepcopy(packet(123)); changed[key] = value
            self.assertNotEqual(business_packet(packet(123)), business_packet(changed))

    def test_inconsistent_identity_or_transport_rejected(self):
        for key, value in (('packet_id', 456), ('payload_hex', packet(456)['payload_hex']),
                           ('expected_transport_hex', packet(456)['expected_transport_hex'])):
            changed = copy.deepcopy(packet(123)); changed[key] = value
            with self.assertRaises(ValueError): business_packet(changed)
