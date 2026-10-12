"""独立业务完整配对、未知费用、两部署变异与共享缓存年龄。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'experiments'))
from test_observer_comparison import fixture
from test_observer_training_diagnostics import packet
from observer_comparison_pairs import summarize, mean_bounds
from check_observer_comparison_analysis import check_pairs, check_integrals
from analyze_observer_comparison import age_bounds


def values(plan):
    result=[]
    for e in plan['execution_order']:
        r={k:e[k] for k in ('scenario','budget_level','seed','block','method','parameter_index','position')}
        r.update(case=e['name'],controller_loss_lower=.8,controller_loss_upper=.81,
            collector_loss_lower=.5,collector_loss_upper=.51,window_feasibility='within',
            api_bytes_per_s=0 if e['method']=='static' else 300,local_notice_bytes_per_s=0)
        for k in ('e1','e2','e3','export'):
            r[k+'_lower_bytes_per_s']=100; r[k+'_upper_bytes_per_s']=101
        if e['method']=='phase_guard':r.update(controller_loss_lower=.7,controller_loss_upper=.71)
        result.append(r)
    return result


def csv_values(table):
    return [{k:'' if v is None else str(v) for k,v in r.items()} for r in table]


class ObserverComparisonAnalysisTests(unittest.TestCase):
    def verify(self,mutate=None,failed=False,unknown=False):
        plan=fixture(); table=values(plan)
        if failed:
            for r in table:
                if r['method']=='static' and r['scenario']=='switch':r['window_feasibility']='exceeds'
        if unknown:
            r=next(r for r in table if r['method']=='phase_guard' and r['scenario']=='shared_priority')
            r.update(window_feasibility='unavailable',e3_lower_bytes_per_s=None,e3_upper_bytes_per_s=None)
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);(out/'comparison-plan.json').write_text(json.dumps(plan))
            for c in plan['execution_order']:
                d=out/c['name'];d.mkdir()
                (d/'packets.json').write_text(json.dumps([packet(123)]));(d/'tasks.json').write_text('[]')
                (d/'collector.jsonl').write_text(json.dumps(dict(ether_type=0x88b6,packet_id=123,records=[dict(switch_id=1)]))+'\n')
                (d/'decisions.jsonl').write_text(json.dumps(dict(scheduled_offset_us=0,skipped=False,selected={'config':[50000]*4},writes=[]))+'\n')
            findings=summarize(out,plan['execution_order'],table)
            self.assertEqual((findings['paired_cases'],findings['business_pair_groups'],findings['deployment_repeat_groups']),(224,112,128))
            self.assertEqual(findings['both_within_pairs'],168 if failed else 223 if unknown else 224)
            if mutate:mutate(out,table)
            return check_pairs(out,plan['execution_order'],csv_values(table))

    def test_complete_four_business_two_block_comparison(self):self.assertGreater(self.verify(),9000)
    def test_single_layer_unknown_cost_keeps_full_case(self):self.assertGreater(self.verify(unknown=True),9000)
    def test_static_budget_failure_preserves_all_pairs(self):self.assertGreater(self.verify(failed=True),9000)

    def corrupt(self,filename,fn):
        def mutate(out,table):
            path=out/filename; data=json.loads(path.read_text());fn(data);path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):self.verify(mutate)

    def test_missing_pair_rejected(self):self.corrupt('paired-cases.json',lambda v:v.pop())
    def test_forged_controller_gain_rejected(self):self.corrupt('paired-cases.json',lambda v:v[0].update(controller_midpoint_relative_reduction=.9))
    def test_duplicate_business_group_rejected(self):self.corrupt('business-pairs.json',lambda v:v.__setitem__(1,v[0]))
    def test_forged_repeated_variation_rejected(self):self.corrupt('deployment-repeats.json',lambda v:v[0].update(controller_midpoint_range=1))
    def test_deployments_not_independent_businesses(self):self.corrupt('paired-groups.json',lambda v:v[0].update(business_count=8))
    def test_unknown_cost_not_averaged_as_zero(self):
        self.assertEqual(mean_bounds([[None,None],[1,2]]),[None,None])
        self.assertEqual(mean_bounds([[1,2],[3,4]]),[2,3])
    def test_wrong_summary_identity_rejected(self):
        def mutate(out,table):table[0]['seed']=3001
        with self.assertRaises(ValueError):self.verify(mutate)
    def test_cache_age_changes_at_actual_entry(self):
        task=dict(object_id=dict(node=1,port=2,queue=0),start_us=0,end_us=1000000,target_us=1000000,weight=1)
        sample=dict(node=1,generation_lower_ns=0,generation_upper_ns=0,entered_lower_ns=100000000,entered_upper_ns=100000000)
        result=age_bounds([task],[sample],0)
        self.assertAlmostEqual(result['loss_lower'],.895);self.assertAlmostEqual(result['loss_upper'],.895)
        self.assertEqual(check_integrals([task],[sample],0,result),6)
    def test_unknown_active_task_remains_in_denominator(self):
        tasks=[dict(object_id=dict(node=n,port=2,queue=0),start_us=0,end_us=1000000,target_us=1000000,weight=1) for n in (1,2)]
        sample=dict(node=1,generation_lower_ns=0,generation_upper_ns=0,entered_lower_ns=100000000,entered_upper_ns=100000000)
        result=age_bounds(tasks,[sample],0)
        self.assertAlmostEqual(result['loss_lower'],(.895+4)/2)
        self.assertEqual(result['weighted_duration_us'],2000000)
