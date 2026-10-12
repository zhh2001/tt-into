import unittest
from orchestration.deployment import KEYS, activation_plan, parameters, transition, interval_margin


def describe(values):
    return [{'node': n, 'flow': f, 'threshold_us': v} for (n, f), v in zip(KEYS, values)]


class DeploymentTests(unittest.TestCase):
    def test_cold_capacity_and_late_update_keep_gate_state(self):
        stages = activation_plan(describe([1]*5), describe([500001, 500001, 100001, 100001, None]), 1)
        self.assertEqual([len(s['writes']) for s in stages], [1, 2, 2, 5])
        self.assertEqual(stages[-1]['writes'][-1].register, 'TTEgress.max_count')
        self.assertEqual(stages[-1]['writes'][-1].value, 0)
        self.assertFalse(any('timestamp' in w.register or 'has_sample' in w.register
                             for s in stages for w in s['writes']))

    def test_piggyback_stages_and_enable_order(self):
        stages = activation_plan(describe([1, 1, 100001, None, None]),
                                 describe([500001, 500001, 200001, 200001, None]), 2)
        self.assertEqual([len(s['writes']) for s in stages], [0, 2, 1, 5])
        self.assertEqual([(w.register, w.value) for w in stages[-1]['writes'][-2:]],
                         [('TTEgress.time_threshold', 200001), ('TTEgress.max_count', 2)])

    def test_unchanged_parameters_cost_no_calls(self):
        p = parameters(describe([1, 1, 100001, None, None]))
        self.assertEqual(transition(p, p, 1), [])

    def test_reenable_must_rewrite_a_stale_threshold(self):
        old = dict.fromkeys(KEYS, None)
        new = {**old, (3, 2): 1}
        writes = transition(old, new, 1)
        # 关闭状态不透露寄存器旧值，重新启用必须明确写入阈值。
        self.assertEqual([w.register for w in writes],
                         ['TTEgress.time_threshold', 'TTEgress.max_count'])

    def test_invalid_parameters(self):
        for rows in [describe([1]*4), describe([1, 1, 1, 1, 0]), describe([1, 1, 1, 1, True])]:
            with self.assertRaises(ValueError):
                parameters(rows)
        p = parameters(describe([1]*5))
        with self.assertRaises(ValueError):
            transition(p, p, 3)

    def test_margin_preserves_the_nominal_packet_schedule(self):
        from dataclasses import replace
        from orchestration.path_reference import fork_packets, replay, Policy
        early = describe([1, 1, 100001, None, None])
        late = describe([500001, 500001, 200001, 200001, None])
        outcomes = []
        for margin in (0, 50000):
            e, l = interval_margin(early, margin), interval_margin(late, margin)
            stages = activation_plan(e, l, 2)
            schedule = [(-800000, Policy(dict.fromkeys(KEYS, None)))] + [
                (s['offset_us'] - 800000, Policy(s['parameters'])) for s in stages]
            result = replay([replace(p, capacity=2) for p in fork_packets()], schedule)
            outcomes.append({k: result[k] for k in ('selected', 'inband_per_link_bytes', 'export_bytes')})
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual([r['threshold_us'] for r in interval_margin(late, 50000)],
                         [550000, 550000, 250000, 250000, None])

    def test_invalid_margin(self):
        for margin in (-1, 100000, 0.5):
            with self.assertRaises(ValueError):
                interval_margin(describe([1]*5), margin)
