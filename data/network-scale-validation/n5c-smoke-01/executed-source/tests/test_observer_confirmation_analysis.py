"""独立六部署排序及失败/未知保留的来源破坏检查。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
from check_observer_confirmation_analysis import check_selection
from check_observer_confirmation_analysis import check_repeats
from observer_confirmation_repeats import summarize
from observer_confirmation_contract import select
from test_observer_confirmation import fixture
from test_observer_training_diagnostics import packet


class ObserverConfirmationAnalysisTests(unittest.TestCase):
    def verify(self, mutate=None, failure=False, unknown=False, missing=False):
        plan, values=fixture()
        for row in values:
            if row['role']=='training_selected' and row['seed']==29 and row['block']==3:
                if failure: row['window_feasibility']='exceeds'
                if unknown: row['export_upper_bytes_per_s']=None
        scores,winners=select(plan,values)
        objects={'confirmation-plan.json':plan,'confirmation-scores.json':scores,'frozen-static.json':winners,
                 'findings.json':{'selected_candidates':{w['scenario']:w['selected_candidate'] for w in winners}}}
        if mutate: mutate(objects)
        table=[{k:'' if v is None else str(v) for k,v in row.items()} for row in values]
        if missing: table.pop()
        with tempfile.TemporaryDirectory() as name:
            output=Path(name)
            for filename,value in objects.items(): (output/filename).write_text(json.dumps(value))
            return check_selection(output,table)

    def test_complete_independent_selection(self):
        self.assertGreater(self.verify(),150)

    def test_wrong_frozen_candidate_rejected(self):
        def mutate(o): o['frozen-static.json'][0]['selected_candidate']=1
        with self.assertRaises(ValueError): self.verify(mutate)

    def test_forged_worst_deployment_rejected(self):
        def mutate(o): o['confirmation-scores.json'][0]['rank'][0]=0
        with self.assertRaises(ValueError): self.verify(mutate)

    def test_single_failure_not_averaged(self):
        self.assertGreater(self.verify(failure=True),150)

    def test_unknown_fee_not_zero_filled(self):
        self.assertGreater(self.verify(unknown=True),150)

    def test_missing_deployment_rejected(self):
        with self.assertRaises(ValueError): self.verify(missing=True)

    def verify_repeats(self, mutate=None):
        plan,_=fixture()
        cases=plan['execution_order']
        with tempfile.TemporaryDirectory() as name:
            output=Path(name)
            (output/'shortlist.json').write_text(json.dumps(plan['shortlist']))
            for c in cases:
                directory=output/c['name'];directory.mkdir()
                for filename,value in (('packets.json',[packet(123)]),('tasks.json',[]),
                    ('generation-bounds.json',{'loss_lower':.2,'loss_upper':.3}),
                    ('controller-visible-bounds.json',{'loss_lower':.4,'loss_upper':.5}),
                    ('window-costs.json',{'window_feasibility':'within'})):
                    (directory/filename).write_text(json.dumps(value))
                (directory/'collector.jsonl').write_text(json.dumps({'ether_type':0x88b6,'packet_id':123,'records':[{'switch_id':1}]})+'\n')
            summary=summarize(output,cases)
            self.assertEqual(summary['configuration_business_groups'],24)
            self.assertEqual(summary['candidate_pairs'],48)
            if mutate: mutate(output)
            return check_repeats(output,cases)

    def test_complete_independent_repeat_groups_and_pairs(self):
        self.assertGreater(self.verify_repeats(),800)

    def test_removed_candidate_pair_rejected(self):
        def mutate(output):
            p=output/'candidate-repeat-pairs.json';values=json.loads(p.read_text());values.pop();p.write_text(json.dumps(values))
        with self.assertRaises(ValueError): self.verify_repeats(mutate)

    def test_forged_repeat_overlap_rejected(self):
        def mutate(output):
            p=output/'seed-repeat-diagnostics.json';values=json.loads(p.read_text());values[0]['controller_common_overlap']=False;p.write_text(json.dumps(values))
        with self.assertRaises(ValueError): self.verify_repeats(mutate)
