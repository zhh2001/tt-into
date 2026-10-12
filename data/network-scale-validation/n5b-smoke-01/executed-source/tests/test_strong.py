"""筛选窗口、反馈租期、有限容量概率和精简格式的边界检查。"""
from pathlib import Path
import sys
import unittest
from fractions import Fraction
import json
import tempfile
from scapy.all import Ether, IP, Raw
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from collector.strong_codec import FORMATS, decode_frame
from orchestration.strong import FlowFilter, BooleanFeedback
sys.path.insert(0,str(ROOT/'experiments'))
from analyze_strong import ages
from orchestration.metrics import age_metrics
from orchestration.model import Task


class StrongTests(unittest.TestCase):
    def test_deviation_rejects_burst_after_two_windows(self):
        f=FlowFilter(100000,30,60000)
        for i in range(1,4):
            smooth=f.update(1,i*16,0,i*400000000,0)
            burst=f.update(2,i*16,i*130000,i*400000000,0)
        self.assertTrue(smooth['eligible'])
        self.assertEqual(smooth['ratio_n'],2)
        self.assertTrue(burst['removed'])
        self.assertFalse(burst['eligible'])

    def test_nonconsecutive_failure_does_not_remove(self):
        f=FlowFilter(100000,30,60000,checks=1)
        self.assertFalse(f.update(1,1,0,400000000,0)['removed'])
        self.assertTrue(f.update(1,17,0,800000000,0)['eligible'])
        self.assertFalse(f.update(1,18,0,1200000000,0)['removed'])

    def test_counter_reset_is_not_a_valid_observation(self):
        f=FlowFilter(100000,30,60000)
        f.update(1,16,0,400000000,0)
        with self.assertRaises(ValueError): f.update(1,0,0,800000000,0)

    def test_feedback_stale_and_lease_expiry(self):
        f=BooleanFeedback({1:100000})
        self.assertEqual(f.tick(200000000,{1:0}),[])
        self.assertEqual(f.tick(210000000,{1:0}),[(1,True)])
        self.assertEqual(f.tick(300000000,{1:300000000}),[])
        self.assertEqual(f.tick(410000000,{1:400000000}),[(1,False)])

    def test_stale_refreshes_feedback_lease(self):
        f=BooleanFeedback({1:100000})
        f.tick(210000000,{1:0})
        f.tick(400000000,{1:0})
        self.assertEqual(f.tick(410000000,{1:410000000}),[])
        self.assertEqual(f.tick(600000000,{1:590000000}),[(1,False)])

    def test_weighted_stream_marginals_by_exhaustive_branches(self):
        distribution={1:Fraction(1)}
        total=3
        for node,weight in [(2,2),(3,1)]:
            total+=weight
            replacement=Fraction(weight,total)
            distribution={n:p*(1-replacement) for n,p in distribution.items()}
            distribution[node]=replacement
        self.assertEqual(distribution,{1:Fraction(1,2),2:Fraction(1,3),3:Fraction(1,6)})

    def test_all_formats_preserve_task_state_and_payload(self):
        item=bytes([2,2])+1234 .to_bytes(6,'big')+7 .to_bytes(4,'big')
        for fmt in FORMATS.values():
            head=bytes([1,64,fmt['version'],1])
            if fmt['header']==8: head+=bytes([255,5,0,9])
            if fmt['header']==5: head+=bytes([5])
            record=item+(bytes(8) if fmt['record']==20 else b'')
            raw=bytes(Ether(type=0x88b6)/IP()/Raw(head+record+b'payload'))
            row=decode_frame(raw)
            self.assertEqual(row['records'],[{'switch_id':2,'hop':2,'generated_us':1234,'queue_packets':7}])
            self.assertEqual(row['transport_hex'],b'payload'.hex())
            self.assertTrue(row['ip_checksum_ok'])

    def test_truncation_and_wrong_versions_rejected(self):
        for content in [bytes([1,64,2,1,255,0,0,0]),bytes([0,64,99,1])]:
            with self.assertRaises(ValueError): decode_frame(bytes(Ether(type=0x88b6)/IP()/Raw(content)))

    def test_causal_age_bounds_with_arbitrary_switch_clock_jump(self):
        packets=[{'packet_id':1},{'packet_id':2}]
        sends=[{'packet_id':1,'send_before_monotonic_ns':1000000000},
               {'packet_id':2,'send_before_monotonic_ns':1400000000}]
        reports={i:{'packet_id':i,'receive_monotonic_ns':t,
                    'records':[{'switch_id':n,'generated_us':(1<<48)-i} for n in [1,2,3]]}
                 for i,t in [(1,1020000000),(2,1420000000)]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'send.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in sends))
            result=ages({'name':'feedback_base'},path,packets,reports)
        for row in result['tasks']:
            exact=age_metrics(Task(row['node'],row['deadline_us']),[(10000,20000),(410000,420000)],0,600000)
            self.assertLessEqual(row['violation_lower'],exact['violation_fraction'])
            self.assertGreaterEqual(row['violation_upper'],exact['violation_fraction'])
        self.assertFalse(result['switch_timestamps_used_for_cross_device_age'])


if __name__=='__main__': unittest.main()
