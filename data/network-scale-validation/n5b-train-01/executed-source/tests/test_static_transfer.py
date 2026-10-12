"""静态迁移诊断使用真实纳秒日程，离线包身份不参与配置选择。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'experiments'))
from diagnose_static_transfer import signature


class TransferTests(unittest.TestCase):
    def fixture(self,d,pid=1,empty=False):
        (d/'packets.json').write_text(json.dumps([{'packet_id':pid,'flow':1,'offset_ns':1000000,'ip_length':120,'path':[1,3]}]))
        report = {'ether_type':0x88b6,'packet_id':pid,'records':[{'switch_id':1,'hop':1}]}
        (d/'collector.jsonl').write_text('' if empty else json.dumps(report)+'\n')
        return report

    def test_actual_nanosecond_schedule_and_normalized_packet_identity(self):
        with tempfile.TemporaryDirectory() as name:
            d=Path(name); a=d/'a'; b=d/'b'; a.mkdir(); b.mkdir()
            self.fixture(a,1); self.fixture(b,500)
            self.assertEqual(signature(a),signature(b))
            self.assertEqual(signature(a)[0][0]['offset_ns'],1000000)

    def test_empty_reports_keep_workload(self):
        with tempfile.TemporaryDirectory() as name:
            d=Path(name); self.fixture(d,empty=True)
            workload,samples=signature(d)
            self.assertEqual(len(workload),1)
            self.assertEqual(samples,[])

    def test_unmatched_packet_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            d=Path(name); r=self.fixture(d); r['packet_id']=2
            (d/'collector.jsonl').write_text(json.dumps(r)+'\n')
            with self.assertRaises(ValueError): signature(d)

    def test_duplicate_sampling_position_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            d=Path(name); r=self.fixture(d)
            (d/'collector.jsonl').write_text((json.dumps(r)+'\n')*2)
            with self.assertRaises(ValueError): signature(d)


if __name__ == '__main__':
    unittest.main()
