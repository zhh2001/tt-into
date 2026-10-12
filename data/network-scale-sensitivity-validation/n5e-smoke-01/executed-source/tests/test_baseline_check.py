"""核对跨流共享、逐流阈值、容量禁用和权限切换的手算轨迹。"""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
from orchestration.baseline_check import predict
from run_baseline_validation import configuration


class BaselineCheckTests(unittest.TestCase):
    def run_inputs(self, config, times, flows, changes=None, length=120):
        packets = [{'packet_id': i, 'flow': flow, 'ip_length': length,
                    'configuration': (changes or {}).get(i, config)} for i, flow in enumerate(flows, 1)]
        traces = []
        for node in range(3):
            gate = [0] + [t + node for t in times]
            traces.append({'trace_arrival': gate, 'trace_generated': gate, 'trace_value': [0] * (len(gate))})
        return predict(packets, traces, 3)

    def test_shared_flow_opportunities_use_one_state(self):
        result = self.run_inputs(configuration('port', [100, 100, 100]), [10, 20], [1, 2])
        self.assertEqual([r['switch_id'] for r in result['packets'][1]['records']], [1])
        self.assertEqual([r['switch_id'] for r in result['packets'][2]['records']], [2])
        self.assertEqual(result['states'][0], {258: 10})

    def test_flow_thresholds_are_independent(self):
        cfg = configuration(thresholds=[{'1': 100, '2': 10, '3': 1}] * 3)
        result = self.run_inputs(cfg, [10, 20, 40, 50], [1, 2, 1, 2])
        self.assertEqual([r['switch_id'] for r in result['packets'][4]['records']], [1])
        self.assertEqual(result['states'][0], {1: 10, 2: 50})

    def test_zero_capacity_does_not_update_success(self):
        cfg = configuration(thresholds=[1, 1, 1], capacities=[0, 1, 1])
        result = self.run_inputs(cfg, [10], [1])
        self.assertEqual(result['states'][0], {})
        self.assertEqual(result['packets'][1]['records'][0]['switch_id'], 2)

    def test_authorization_keeps_previous_success(self):
        first = configuration(thresholds=[100, 100, 100], masks={1: 1}, epoch=1)
        second = configuration(thresholds=[100, 100, 100], masks={1: 2}, epoch=2)
        result = self.run_inputs(first, [10, 20, 40], [1, 1, 1], {2: second})
        self.assertEqual(result['packets'][3]['records'], [])
        self.assertEqual(result['states'][0][1], 10)

    def test_permission_cannot_bypass_mtu(self):
        cfg = configuration(thresholds=[1, 1, 1], masks={1: 4})
        result = self.run_inputs(cfg, [10], [1], length=1473)
        self.assertEqual(result['packets'][1]['records'], [])
        self.assertEqual(result['cost']['inband_per_link_bytes'], [8, 8, 8])


if __name__ == '__main__':
    unittest.main()
