from pathlib import Path
import sys
import unittest
from scapy.all import Ether, IP, Raw
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from collector.state_codec import decode_frame, STATE_TYPE


class StateCodecTests(unittest.TestCase):
    def frame(self, payload):
        return bytes(Ether(src='02:00:00:00:00:01', dst='02:00:00:00:00:03', type=STATE_TYPE) /
                     IP(src='10.0.0.1', dst='10.0.0.2', proto=17) / Raw(payload))

    def test_generation_width_and_report_without_transport(self):
        generation = (1 << 48) - 1
        record = b'\x02\x02' + generation.to_bytes(6, 'big') + (123).to_bytes(4, 'big') + (7).to_bytes(8, 'big')
        parsed = decode_frame(self.frame(b'\x01\x40\x01\x01\xff\x00\x00\x00' + record))
        self.assertEqual(parsed['records'][0]['generated_us'], generation)
        self.assertEqual(parsed['records'][0]['queue_packets'], 123)
        self.assertEqual(parsed['records'][0]['selection_key'], 7)
        self.assertEqual(parsed['transport_hex'], '')
        self.assertTrue(parsed['ip_checksum_ok'])

    def test_reject_unknown_version(self):
        with self.assertRaises(ValueError):
            decode_frame(self.frame(b'\x00\x40\x02\x01\xff\x00\x00\x00'))

    def test_reject_truncated_item(self):
        with self.assertRaises(ValueError):
            decode_frame(self.frame(b'\x01\x40\x01\x01\xff\x00\x00\x00' + b'\x01' * 19))
