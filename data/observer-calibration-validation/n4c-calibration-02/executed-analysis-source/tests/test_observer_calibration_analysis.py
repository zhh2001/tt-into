"""全局排序、完整静态配对和观察积分的独立来源检查。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'experiments'))
from test_observer_calibration import fixture
from observer_calibration_contract import select
from observer_calibration_pairs import summarize
from check_observer_calibration_analysis import check_selection,check_pairs,check_integrals
from analyze_observer_calibration import age_bounds
from test_observer_training_diagnostics import packet


def summaries(values):
    fields=('case','scenario','budget_level','seed','method','parameter_index','successful_updates','window_feasibility',
        'controller_loss_lower','controller_loss_upper','collector_loss_lower','collector_loss_upper',
        'e1_upper_bytes_per_s','e2_upper_bytes_per_s','e3_upper_bytes_per_s','export_upper_bytes_per_s',
        'api_bytes_per_s','local_notice_bytes_per_s')
    result=[{k:r[k] for k in fields} for r in values]
    for r in result:
        for k in ('e1','e2','e3','export'):r[k+'_lower_bytes_per_s']=r[k+'_upper_bytes_per_s']
    return result


def csv_values(values):
    return [{k:'' if v is None else str(v) for k,v in r.items()} for r in values]


class ObserverCalibrationAnalysisTests(unittest.TestCase):
    def verify_selection(self,mutate=None,failure=False,unknown=False):
        plan,values=fixture()
        if failure:
            for r in values:
                if r['parameter_index']==0 and r['seed']==1301 and r['scenario']=='stable':r['window_feasibility']='exceeds'
        if unknown:
            next(r for r in values if r['method']=='phase_local_guard' and r['parameter_index']==2)['e3_upper_bytes_per_s']=None
        values=summaries(values);scores,winners=select(plan,values)
        objects={'calibration-plan.json':plan,'parameter-scores.json':scores,'frozen-parameters.json':winners,
                 'findings.json':{'selected_parameters':{w['method']:w['selected_parameter'] for w in winners}}}
        table=csv_values(values)
        if mutate:mutate(objects,table)
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            for name,value in objects.items():(folder/name).write_text(json.dumps(value))
            return check_selection(folder,table)

    def test_complete_independent_global_selection(self):self.assertGreater(self.verify_selection(),180)
    def test_single_failed_business_preserved(self):self.assertGreater(self.verify_selection(failure=True),180)
    def test_unknown_clock_condition_cost_retained_without_rank(self):self.assertGreater(self.verify_selection(unknown=True),180)
    def test_wrong_frozen_parameter_rejected(self):
        def mutate(o,t):o['frozen-parameters.json'][0]['selected_parameter']=5
        with self.assertRaises(ValueError):self.verify_selection(mutate)
    def test_forged_controller_score_rejected(self):
        def mutate(o,t):o['parameter-scores.json'][0]['rank'][2]=0
        with self.assertRaises(ValueError):self.verify_selection(mutate)
    def test_forged_fee_excess_rejected(self):
        def mutate(o,t):o['parameter-scores.json'][0]['rank'][1]=.1
        with self.assertRaises(ValueError):self.verify_selection(mutate)
    def test_missing_guarded_business_rejected(self):
        def mutate(o,t):t.remove(next(r for r in t if r['method']=='phase_guard'))
        with self.assertRaises(ValueError):self.verify_selection(mutate)

    def verify_pairs(self,mutate=None):
        plan,values=fixture();values=summaries(values);_,frozen=select(plan,values)
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            (folder/'frozen-parameters.json').write_text(json.dumps(frozen))
            for c in plan['execution_order']:
                d=folder/c['name'];d.mkdir()
                (d/'packets.json').write_text(json.dumps([packet(123)]));(d/'tasks.json').write_text('[]')
                (d/'collector.jsonl').write_text(json.dumps({'ether_type':0x88b6,'packet_id':123,'records':[{'switch_id':1}]})+'\n')
            finding=summarize(folder,plan['execution_order'],values,frozen)
            self.assertEqual(finding['selected_cases'],64);self.assertEqual(finding['pairs'],56)
            if mutate:mutate(folder)
            return check_pairs(folder,plan['execution_order'],csv_values(values))

    def test_complete_independent_static_pairs(self):self.assertGreater(self.verify_pairs(),1300)
    def test_removed_static_pair_rejected(self):
        def mutate(d):
            p=d/'paired-cases.json';v=json.loads(p.read_text());v.pop();p.write_text(json.dumps(v))
        with self.assertRaises(ValueError):self.verify_pairs(mutate)
    def test_forged_controller_improvement_rejected(self):
        def mutate(d):
            p=d/'paired-cases.json';v=json.loads(p.read_text());v[0]['controller_direction']='lower';p.write_text(json.dumps(v))
        with self.assertRaises(ValueError):self.verify_pairs(mutate)
    def test_age_changes_only_when_report_enters_cache(self):
        tasks=[dict(object_id=dict(node=1,port=2,queue=0),start_us=0,end_us=1000000,target_us=1000000,weight=1)]
        samples=[dict(node=1,generation_lower_ns=0,generation_upper_ns=0,entered_lower_ns=100000000,entered_upper_ns=100000000)]
        bounds=age_bounds(tasks,samples,0)
        self.assertAlmostEqual(bounds['loss_lower'],.895)
        self.assertAlmostEqual(bounds['loss_upper'],.895)
        self.assertEqual(check_integrals(tasks,samples,0,bounds),6)
