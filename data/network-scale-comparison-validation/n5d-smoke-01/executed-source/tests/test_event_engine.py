"""通用事件合同、独立手算及旧确定性轨迹回归。"""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from collector.tagged_codec import HEADER_BYTES, WireRecord, decode, encode
from orchestration.event_engine import EventEngine
from orchestration.event_metrics import evaluate_tasks, window_costs
from orchestration.event_observation import estimate_rates
from orchestration.event_schema import FlowId, Hop, Node, ObjectId, Packet, Read, RpcCost, Task, Write
from orchestration.path_reference import Opportunity, Policy, fork_packets, replay

FLOW = FlowId(7, 1001)
COST = RpcCost(10, 4, '手工指定的合成 RPC 费用，不是实测')


def installation(flow, route, threshold=10, time=-20):
    return tuple(w for i, obj in enumerate(route) for w in (
        Write(f'i-{flow.label}-{i}-x', obj.node, flow, 'enabled', True, 1, time, time + 1, time + 2, COST, 'installation'),
        Write(f'i-{flow.label}-{i}-t', obj.node, flow, 'threshold_us', threshold, 1, time, time + 1, time + 2, COST, 'installation')))


def packet(pid, times, route=None, flow=FLOW, arrivals=None, ip_bytes=120, mtus=None):
    route = (ObjectId(1),) if route is None else route
    hops = tuple(Hop(o, f'e{o.node}-{o.port}', a, b, 1500 if mtus is None else mtus[i], i + 3)
                 for i, (o, (a, b)) in enumerate(zip(route, times)))
    sink = times[-1][1] + 1
    return Packet(pid, flow, hops, sink, (sink + 1,) if arrivals is None else arrivals, ip_bytes)


class EventEngineTests(unittest.TestCase):
    def engine(self, packets, route=None, writes=(), reads=(), capacity=1, nodes=None, threshold=10):
        route = tuple(h.object_id for h in packets[0].hops) if route is None else route
        nodes = [Node(o.node) for o in route] if nodes is None else nodes
        return EventEngine(nodes, {FLOW: route}, packets, installation(FLOW, route, threshold) + tuple(writes), reads, capacity)

    def test_codec_golden_bytes_and_concurrent_flow_identity(self):
        flow = FlowId(0x05060708, 0x01020304)
        record = WireRecord(7, 1, 0x010203040506, 0x0A0B0C0D)
        payload = bytes.fromhex('01ff0601010203040506070807010102030405060a0b0c0d')
        self.assertEqual(encode(flow, [record]), payload)
        self.assertEqual(decode(payload), (flow, (record,)))
        self.assertNotEqual(encode(replace(flow, session=1), [record]), payload)
        self.assertNotEqual(encode(replace(flow, tag=1), [record]), payload)
        self.assertEqual(len(payload), 24)

    def test_codec_rejects_truncation_unknown_version_and_overflow(self):
        payload = encode(FLOW, [WireRecord(1, 1, 0, 2)])
        for bad in (payload[:11], payload[:-1], payload + b'\x00', payload[:2] + b'\x09' + payload[3:],
                    b'\x09' + payload[1:]):
            with self.assertRaises(ValueError):
                decode(bad)

    def test_threshold_boundary_first_sample_and_idle(self):
        packets = [packet(i, ((t, t + 1),)) for i, t in enumerate((0, 9, 10, 20), 1)]
        engine = self.engine(packets)
        result = engine.run()
        self.assertEqual(result['selected'], {1: [1], 2: [], 3: [1], 4: [1]})
        self.assertEqual(result['inband_per_link_bytes'], {'e1-2': 84})
        self.assertEqual(result['export_attempt_bytes'], 180)
        empty = EventEngine([Node(1)], {FLOW: (ObjectId(1),)}, (), installation(FLOW, (ObjectId(1),)))
        self.assertEqual(empty.run()['export_attempt_bytes'], 0)

    def test_inflight_policy_read_occurs_at_egress(self):
        p = packet(1, ((0, 10),))
        update = Write('off', 1, FLOW, 'enabled', False, 2, 3, 5, 12, COST)
        engine = self.engine([p], writes=[update])
        before = engine.run_until(6)
        self.assertFalse(any(c.write_id == 'off' for c in before.confirmations))
        result = engine.run()
        self.assertEqual(result['selected'][1], [])
        self.assertIsNone(engine.gates[1, FLOW].last_local_us)
        self.assertEqual(result['control_serialized_bytes_by_kind'], {'installation': 28, 'observation': 0, 'update': 14})
        self.assertEqual(result['issued_write_rpc_count_by_kind']['update'], 1)

    def test_failed_write_is_charged_and_does_not_change_state(self):
        p = packet(1, ((0, 10),))
        failed = Write('failed', 1, FLOW, 'enabled', False, 2, 3, 5, 12, COST, success=False)
        engine = self.engine([p], writes=[failed])
        result = engine.run()
        self.assertEqual(result['selected'][1], [1])
        self.assertEqual(result['control_serialized_bytes_by_kind']['update'], 14)
        self.assertFalse(engine.view().confirmations[-1].success)

    def test_disable_reenable_and_threshold_change_preserve_success(self):
        packets = [packet(i, ((t, t + 1),)) for i, t in enumerate((0, 5, 8, 10), 1)]
        writes = [Write('off', 1, FLOW, 'enabled', False, 2, 2, 3, 3, COST),
                  Write('on', 1, FLOW, 'enabled', True, 3, 6, 7, 7, COST)]
        result = self.engine(packets, writes=writes).run()
        self.assertEqual(result['selected'], {1: [1], 2: [], 3: [], 4: [1]})
        threshold = Write('threshold', 1, FLOW, 'threshold_us', 20, 2, 2, 3, 3, COST)
        result = self.engine([packet(1, ((0, 1),)), packet(2, ((10, 11),))], writes=[threshold]).run()
        self.assertEqual(result['selected'][2], [])

    def test_nonatomic_fields_and_nodes_are_visible(self):
        route = (ObjectId(1), ObjectId(2))
        p = packet(1, ((4, 5), (8, 9)), route)
        updates = [Write('node1-off', 1, FLOW, 'enabled', False, 2, 0, 3, 10, COST),
                   Write('node2-threshold', 2, FLOW, 'threshold_us', 20, 2, 0, 7, 10, COST),
                   Write('node2-off', 2, FLOW, 'enabled', False, 2, 0, 12, 13, COST)]
        result = self.engine([p], writes=updates).run()
        self.assertEqual(result['selected'][1], [2])
        second = next(e for e in result['events'] if e['kind'] == 'egress' and e['node'] == 2)
        self.assertEqual((second['enabled_version'], second['threshold_version']), (1, 2))

    def test_capacity_block_does_not_update_downstream_gate(self):
        route = (ObjectId(1), ObjectId(2))
        packets = [packet(1, ((0, 1), (2, 3)), route), packet(2, ((5, 6), (7, 8)), route)]
        engine = self.engine(packets, route)
        result = engine.run()
        self.assertEqual(result['selected'], {1: [1], 2: [2]})
        self.assertEqual(engine.gates[2, FLOW].last_local_us, 7)
        self.assertEqual(result['inband_per_link_bytes'], {'e1-2': 36, 'e2-2': 48})

    def test_source_mtu_boundary_and_downstream_admission_drop(self):
        too_big = packet(1, ((0, 1),), ip_bytes=1489)
        exact_header = packet(2, ((10, 11),), ip_bytes=1488)
        exact_record = packet(3, ((20, 21),), ip_bytes=1476)
        result = self.engine([too_big, exact_header, exact_record]).run()
        self.assertEqual(result['selected'], {1: [], 2: [], 3: [1]})
        self.assertEqual(result['inband_per_link_bytes'], {'e1-2': 36})
        route = (ObjectId(1), ObjectId(2))
        p = packet(1, ((0, 1), (2, 3)), route, ip_bytes=100, mtus=(1500, 120))
        result = self.engine([p], route).run()
        self.assertEqual(result['dropped_packets'], [1])
        self.assertEqual(result['inband_per_link_bytes'], {'e1-2': 24, 'e2-2': 0})
        self.assertEqual(result['export_attempt_bytes'], 0)

    def test_cross_packet_reordering_changes_downstream_gate(self):
        route = (ObjectId(1), ObjectId(2))
        # 后发包先经过节点 2。整包重放会得到相反的结果。
        packets = [packet(1, ((0, 1), (100, 101)), route),
                   packet(2, ((10, 11), (20, 21)), route)]
        updates = [Write('upstream-off', 1, FLOW, 'enabled', False, 2, -5, -4, -3, COST)]
        result = self.engine(packets, route, writes=updates, threshold=90).run()
        self.assertEqual(result['selected'], {1: [], 2: [2]})
        order = [e['packet_id'] for e in result['events'] if e['kind'] == 'egress' and e['node'] == 2]
        self.assertEqual(order, [2, 1])

    def test_equal_time_causality_and_declared_control_priority(self):
        route = (ObjectId(1), ObjectId(2))
        p = packet(1, ((0, 0), (0, 0)), route)
        update = Write('off-at-zero', 1, FLOW, 'enabled', False, 2, 0, 0, 0, COST)
        result = self.engine([p], route, writes=[update]).run()
        self.assertEqual(result['selected'][1], [2])
        source = next(e for e in result['events'] if e['kind'] == 'egress' and e['node'] == 1)
        downstream = next(e for e in result['events'] if e['kind'] == 'ingress' and e['node'] == 2)
        self.assertLess(result['events'].index(source), result['events'].index(downstream))

    def test_clock_offset_wrap_and_out_of_order_ingress_gate_semantics(self):
        packets = [packet(i, ((t, t + 1),)) for i, t in enumerate((0, 9, 10), 1)]
        base = self.engine(packets).run()['selected']
        wrapped = self.engine(packets, nodes=[Node(1, 250, 8)]).run()['selected']
        self.assertEqual(base, wrapped)
        # 如同实际无符号寄存器减法，较早 ingress 的包晚出队可能产生大差值。
        engine = self.engine([packet(1, ((0, 100),)), packet(2, ((10, 20),))], threshold=50)
        result = engine.run()
        self.assertEqual(result['selected'], {1: [1], 2: [1]})
        self.assertEqual(engine.gates[1, FLOW].last_local_us, 0)

    def test_old_duplicate_reports_do_not_reset_effective_recency(self):
        packets = [packet(1, ((0, 1),), arrivals=(20, 21)), packet(2, ((10, 11),), arrivals=(13, 14))]
        engine = self.engine(packets)
        engine.run_until(22)
        self.assertEqual(engine.view().effective_recency_us(ObjectId(1)), 9)
        self.assertEqual([r['accepted'] for r in engine.receipts], [True, False, False, False])
        self.assertEqual(engine.view().cache[0].generated_local_us, 11)
        self.assertEqual(len(engine.view().reports), 4)

    def test_export_loss_duplicate_and_pre_export_drop_have_distinct_costs(self):
        lost = packet(1, ((0, 1),), arrivals=())
        suppressed = replace(lost, export_emitted=False)
        duplicate = replace(lost, report_arrivals_us=(3, 4))
        self.assertEqual(self.engine([lost]).run()['export_attempt_bytes'], 60)
        self.assertEqual(self.engine([suppressed]).run()['export_attempt_bytes'], 0)
        result = self.engine([duplicate]).run()
        self.assertEqual((result['export_attempt_bytes'], result['received_report_bytes']), (60, 120))

    def test_shared_object_across_flows_and_separate_ports(self):
        other = FlowId(7, 2002)
        shared = (ObjectId(1),)
        packets = [packet(1, ((0, 1),)), packet(2, ((10, 11),), flow=other)]
        engine = EventEngine([Node(1)], {FLOW: shared, other: shared}, packets,
                             installation(FLOW, shared) + installation(other, shared))
        engine.run()
        self.assertEqual(len(engine.view().cache), 1)
        self.assertEqual(len(engine.gates), 2)
        separate = (ObjectId(1, 3),)
        engine = EventEngine([Node(1)], {FLOW: shared, other: separate},
                             [packets[0], packet(2, ((10, 11),), separate, other)],
                             installation(FLOW, shared) + installation(other, separate))
        engine.run()
        self.assertEqual(len(engine.view().cache), 2)

    def test_paid_count_reads_only_become_available_after_response(self):
        packets = [packet(i, ((t, t + 1),)) for i, t in enumerate((0, 10, 20), 1)]
        reads = [Read('r1', FLOW, 1, 2, 5, 7, COST), Read('r2', FLOW, 1, 21, 25, 27, COST)]
        engine = self.engine(packets, reads=reads)
        self.assertEqual(engine.run_until(6).counts, ())
        self.assertIsNone(estimate_rates(engine.run_until(7))[0].rate_pps)
        view = engine.run_until(27)
        self.assertEqual([r.count for r in view.counts], [1, 3])
        self.assertEqual(estimate_rates(view)[0].rate_pps, 100000)
        self.assertEqual(engine.result()['control_serialized_bytes_by_kind']['observation'], 28)
        self.assertFalse(hasattr(view.counts[0], 'snapshot_us'))

    def test_failed_observation_and_same_time_snapshot(self):
        reads = [Read('before', FLOW, 1, -1, 0, 0, COST),
                 Read('failed', FLOW, 1, 2, 3, 4, COST, success=False)]
        engine = self.engine([packet(1, ((0, 1),))], reads=reads)
        engine.run()
        self.assertEqual([r.count for r in engine.view().counts], [0])
        self.assertEqual(engine.result()['control_serialized_bytes_by_kind']['observation'], 28)

    def test_future_suffix_cannot_change_online_view_or_rate_prediction(self):
        prefix = [packet(1, ((0, 1),)), packet(2, ((10, 11),))]
        reads = [Read('r1', FLOW, 1, 2, 3, 4, COST), Read('r2', FLOW, 1, 12, 13, 14, COST)]
        first = self.engine(prefix + [packet(3, ((100, 101),))], reads=reads).run_until(20)
        second = self.engine(prefix + [packet(3, ((50, 51),))], reads=reads).run_until(20)
        self.assertEqual(first, second)
        self.assertEqual(estimate_rates(first), estimate_rates(second))
        self.assertFalse(hasattr(first, 'packets'))
        self.assertFalse(hasattr(first.reports[0], 'packet_id'))
        self.assertFalse(hasattr(first.reports[0].records[0], 'generated_us'))

    def test_known_legacy_fork_trajectory_agrees_without_overwriting_old_model(self):
        legacy = fork_packets(start_us=-200000, end_us=300000)
        thresholds = {(1, 1): 70000, (2, 2): 110000, (3, 1): 1, (3, 2): 1, (3, 3): 1}
        expected = replay(legacy, [(-300000, Policy(thresholds))])
        flows = {i: FlowId(1, i) for i in (1, 2, 3)}
        routes = {flows[i]: tuple(ObjectId(n) for n in next(p.path for p in legacy if p.flow == i)) for i in flows}
        packets = [Packet(p.packet_id, flows[p.flow],
                          tuple(Hop(ObjectId(n), str(e), int(a), int(b)) for n, e, a, b in
                                zip(p.path, p.links, p.gate_us, p.generated_us)),
                          int(p.generated_us[-1]), (int(p.arrival_us),), p.ip_bytes) for p in legacy]
        writes = []
        for (node, flow), threshold in thresholds.items():
            for field, value in [('enabled', True), ('threshold_us', threshold)]:
                writes.append(Write(f'{node}-{flow}-{field}', node, flows[flow], field, value, 1,
                                    -300000, -299999, -299998, COST, 'installation'))
        result = EventEngine([Node(i) for i in (1, 2, 3)], routes, packets, writes).run()
        self.assertEqual(result['selected'], expected['selected'])
        self.assertEqual(list(result['successful_insertions'].values()), expected['successful_insertions'])
        for link in range(3):
            count = sum(link in p.links for p in legacy)
            self.assertEqual(result['inband_per_link_bytes'][str(link)],
                             expected['inband_per_link_bytes'][link] + (HEADER_BYTES - 4) * count)
        self.assertEqual(result['export_attempt_bytes'], expected['export_bytes'])

    def test_more_than_three_nodes_and_flows(self):
        route = tuple(ObjectId(n) for n in range(1, 6))
        flows = [FlowId(1, i) for i in range(20)]
        packets = [packet(i, tuple((i * 20 + j * 2, i * 20 + j * 2 + 1) for j in range(5)), route, f)
                   for i, f in enumerate(flows)]
        result = EventEngine([Node(n) for n in range(1, 6)], dict.fromkeys(flows, route), packets,
                             tuple(w for f in flows for w in installation(f, route)), capacity=8).run()
        self.assertTrue(all(nodes == [5, 4, 3, 2, 1] for nodes in result['selected'].values()))
        self.assertEqual(result['allocated_gate_keys'], 100)
        self.assertEqual(result['export_attempt_bytes'], 20 * 106)

    def test_rejects_noninteger_time_path_changes_and_invalid_registration(self):
        with self.assertRaises(ValueError):
            Hop(ObjectId(1), 'e', 0.5, 1)
        with self.assertRaises(ValueError):
            self.engine([packet(1, ((0, 1),))], route=(ObjectId(2),))
        with self.assertRaises(ValueError):
            self.engine([packet(1, ((0, 1),))], reads=[Read('bad', FLOW, 2, 0, 1, 2, COST)])
        with self.assertRaises(ValueError):
            self.engine([packet(1, ((0, 1),)), packet(2, ((128, 129),))], nodes=[Node(1, 0, 8)])


class EventMetricTests(unittest.TestCase):
    def test_hand_integrals_old_report_and_effective_recency(self):
        obj = ObjectId(1)
        receipts = [{'object_id': obj, 'generated_us': g, 'received_us': a, 'accepted': ok}
                    for g, a, ok in ((0, 0, True), (4, 6, True), (1, 8, False), (4, 9, False))]
        result = evaluate_tasks([Task('t', obj, 0, 10, 5)], receipts, clip=2)
        row = result['tasks'][0]
        self.assertAlmostEqual(result['weighted_clipped_normalized_loss'], 0.68)
        self.assertAlmostEqual(row['generation_age']['mean_age_us'], 3.4)
        self.assertAlmostEqual(row['generation_age']['violation_fraction'], 0.2)
        self.assertAlmostEqual(row['effective_receipt_recency']['mean_age_us'], 2.6)
        self.assertEqual(row['first_effective_report_wait_us'], 0)

    def test_unknown_clip_inactive_denominator_and_no_report_task(self):
        first, missing = ObjectId(1), ObjectId(2)
        receipts = [{'object_id': first, 'generated_us': 1, 'received_us': 3, 'accepted': True}]
        tasks = [Task('first', first, 0, 10, 2), Task('missing', missing, 0, 5, 2, weight=2)]
        result = evaluate_tasks(tasks, receipts, clip=2)
        self.assertAlmostEqual(result['tasks'][0]['clipped_normalized_loss'], 1.9)
        self.assertAlmostEqual(result['weighted_clipped_normalized_loss'], 1.95)
        self.assertAlmostEqual(result['weighted_active_interval_coverage'], 1 / 3)
        self.assertIsNone(result['tasks'][0]['generation_age']['p95_age_us'])
        self.assertIsNone(result['tasks'][1]['generation_age']['mean_age_us'])
        self.assertEqual(result['tasks'][1]['generation_age']['unknown_fraction'], 1)

    def test_shared_object_duplicates_rejected_and_phase_history_kept(self):
        obj = ObjectId(1)
        with self.assertRaises(ValueError):
            evaluate_tasks([Task('a', obj, 0, 10, 2), Task('b', obj, 5, 15, 2)], [])
        receipt = [{'object_id': obj, 'generated_us': 0, 'received_us': 1, 'accepted': True}]
        result = evaluate_tasks([Task('first', obj, 0, 5, 10), Task('second', obj, 5, 10, 10)], receipt)
        self.assertEqual(result['tasks'][1]['generation_age']['unknown_fraction'], 0)
        self.assertEqual(result['tasks'][1]['first_effective_report_wait_us'], 0)
        self.assertFalse(result['tasks'][1]['active_interval_effective_coverage'])

    def test_half_open_cost_windows_actual_overrun_and_control_basis(self):
        events = [{'time_us': t, 'resource': 'inband', 'link': 'e', 'bytes': b, 'basis': 'ipv4_increment'}
                  for t, b in ((0, 12), (999999, 24), (1000000, 100))]
        events += [{'time_us': 1, 'resource': 'export', 'bytes': 60, 'basis': 'ethernet_without_fcs_with_padding'},
                   {'time_us': 2, 'resource': 'control', 'kind': 'observation', 'bytes': 14, 'basis': 'serialized_rpc'}]
        row = window_costs(events, 0, 1000000, {'e': 20}, 50)
        self.assertEqual(row['inband_per_link_bytes'], {'e': 36})
        self.assertEqual(row['inband_over_budget_bytes_per_s'], {'e': 16})
        self.assertEqual(row['export_over_budget_bytes_per_s'], 10)
        self.assertEqual(row['control_serialized_bytes_by_kind']['observation'], 14)

    def test_control_entry_is_not_rpc_count_and_mixed_basis_is_rejected(self):
        events = [{'time_us': 0, 'resource': 'control', 'kind': 'update', 'part': 'request',
                   'node': 1, 'flow': '1:1', 'bytes': 10, 'basis': 'serialized_rpc'} for _ in range(2)]
        row = window_costs(events, 0, 1000000)
        self.assertEqual(row['updated_entry_count'], 1)
        self.assertEqual(row['control_request_count_by_kind']['update'], 2)
        with self.assertRaises(ValueError):
            window_costs([dict(events[0], basis='ethernet_without_fcs')], 0, 1000000)

    def test_independent_checker_rejects_cost_and_age_corruption(self):
        import copy
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
        from run_event_validation import cases, plain
        from check_event_validation import check_case
        spec = next(cases())
        engine = EventEngine(spec['nodes'], spec['routes'], spec['packets'], spec['writes'], spec['reads'], spec['capacity'])
        result = engine.run()
        result['task_metrics'] = evaluate_tasks(spec['tasks'], result['receipts'], spec['clip'])
        serialized = {k: v for k, v in spec.items() if k != 'routes'}
        serialized['routes'] = [{'flow': f, 'objects': route} for f, route in spec['routes'].items()]
        spec, result = plain(serialized), plain(result)
        self.assertGreater(len(check_case(spec, result)), 10)
        broken = copy.deepcopy(result)
        broken['export_attempt_bytes'] -= 1
        with self.assertRaises(ValueError):
            check_case(spec, broken)
        broken = copy.deepcopy(result)
        broken['task_metrics']['weighted_clipped_normalized_loss'] -= 0.1
        with self.assertRaises(ValueError):
            check_case(spec, broken)


if __name__ == '__main__':
    unittest.main()
