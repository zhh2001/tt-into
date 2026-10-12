"""检查独立采集器对报文完整性和报告格式的判断。"""
from pathlib import Path
import sys
import unittest

from scapy.all import Ether, IP, Raw, UDP

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from collector.codec import checksum, decode_frame, TELEMETRY_TYPE

MACS = {"src": "02:00:00:00:00:01", "dst": "02:00:00:00:00:02"}


class CodecTests(unittest.TestCase):
    def test_plain_udp(self):
        frame = bytes(Ether(**MACS) / IP(src="10.0.0.1", dst="10.0.0.2", id=19) / UDP() / Raw(b"abcdef"))
        decoded = decode_frame(frame)
        self.assertEqual(decoded["packet_id"], 19)
        self.assertTrue(decoded["ip_checksum_ok"])

    def test_truncated_report_without_transport(self):
        frame = bytes(Ether(type=TELEMETRY_TYPE, **MACS) / IP(src="10.0.0.1", dst="10.0.0.2", proto=17) /
                      Raw(bytes([2, 64, 5, 5, 1, 1])))
        decoded = decode_frame(frame)
        self.assertEqual(decoded["records"], [{"switch_id": 5, "hop": 5}, {"switch_id": 1, "hop": 1}])
        self.assertEqual(decoded["transport_hex"], "")

    def test_report_count_overflow(self):
        frame = bytes(Ether(type=TELEMETRY_TYPE, **MACS) / IP() / Raw(bytes([9, 64]) + b"\0" * 18))
        with self.assertRaises(ValueError):
            decode_frame(frame)

    def test_report_underflow(self):
        frame = bytes(Ether(type=TELEMETRY_TYPE, **MACS) / IP() / Raw(bytes([2, 64, 1, 1])))
        with self.assertRaises(ValueError):
            decode_frame(frame)

    def test_ipv4_length_underflow(self):
        frame = bytes(Ether(**MACS) / IP(len=100) / Raw(b"abcd"))
        with self.assertRaises(ValueError):
            decode_frame(frame)

    def test_corrupt_checksum_detected(self):
        frame = bytearray(bytes(Ether(**MACS) / IP() / UDP() / Raw(b"payload")))
        frame[24] ^= 0xFF
        self.assertFalse(decode_frame(bytes(frame))["ip_checksum_ok"])

    def test_odd_checksum_length(self):
        self.assertEqual(checksum(b"\x01\x02\x03"), 0xFBFD)


if __name__ == "__main__":
    unittest.main()
