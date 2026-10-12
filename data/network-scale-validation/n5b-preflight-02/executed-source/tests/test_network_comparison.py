"""新业务消费边界、完整比较矩阵及独立配对/部署分母核对。"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'experiments'))
from network_comparison_contract import audit_consumption, expected_order, validate_plan, METHOD_SHA256
from network_comparison_pairs import summarize_pairs
from check_network_comparison_analysis import check_pairs


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.contract=json.loads((ROOT/'data/network-comparison-plan/n3h-methods-01/method-contract.json').read_text())
        self.seeds=[3001,3002,3003,3004]
        self.plan={'status':'prepared','execution_ready':True,'expected_cases':256,'business_count':4,
            'deployment_blocks':2,'fresh_inputs_generated':False,'order_seed':42426407,
            'fresh_business_seeds':self.seeds,'method_contract_sha256':METHOD_SHA256,
            'method_contract':self.contract,'seed_audit':{'selected_fresh_seeds':self.seeds,'excluded_seeds':[11,29,1301,1423]},
            'source_sha256':{},'execution_order':expected_order(self.contract,self.seeds)}

    def test_complete_four_business_two_blocks(self):
        validate_plan(self.plan)
        self.assertEqual(len({r['name'] for r in self.plan['execution_order']}),256)
        for seed in self.seeds:
            for block in (1,2): self.assertEqual(sum(r['seed']==seed and r['block']==block for r in self.plan['execution_order']),32)

    def test_fixed_global_parameters_across_profiles(self):
        for method,index in (('uniform_guard',3),('phase_guard',2),('phase_local_guard',4)):
            self.assertEqual({r['parameter_index'] for r in self.plan['execution_order'] if r['method']==method},{index})

    def test_removed_case_rejected(self):
        self.plan['execution_order'].pop()
        with self.assertRaises(ValueError): validate_plan(self.plan)

    def test_duplicate_identity_rejected(self):
        self.plan['execution_order'][-1]=copy.deepcopy(self.plan['execution_order'][0])
        with self.assertRaises(ValueError): validate_plan(self.plan)

    def test_changed_block_rejected(self):
        self.plan['execution_order'][0]['block']=3
        with self.assertRaises(ValueError): validate_plan(self.plan)

    def test_consumed_seed_rejected(self):
        self.plan['seed_audit']['excluded_seeds'].append(3001)
        with self.assertRaises(ValueError): validate_plan(self.plan)

    def test_changed_method_parameter_rejected(self):
        self.plan['method_contract']['methods']['phase_guard']['adaptive']['gain_margin']=.15
        with self.assertRaises(ValueError): validate_plan(self.plan)

    def test_changed_initialization_rejected(self):
        self.plan['method_contract']['profiles'][0]['initial_config'][0]=100000
        with self.assertRaises(ValueError): validate_plan(self.plan)

    def test_changed_budget_rejected(self):
        self.plan['method_contract']['budget']['low']['export']=900
        with self.assertRaises(ValueError): validate_plan(self.plan)

    def test_duplicate_business_not_independent(self):
        with self.assertRaises(ValueError): expected_order(self.contract,[3001,3001,3002,3003])

    def test_reserved_not_consumed_but_generated_is(self):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp)/'data';data.mkdir()
            (data/'manifest.json').write_text(json.dumps({'status':'prepared','reserved_new_groups':{'test':[3001]}}))
            audit=audit_consumption(data)
            self.assertIn(3001,audit['reserved_seeds']);self.assertNotIn(3001,audit['excluded_seeds'])
            (data/'frozen-inputs.json').write_text(json.dumps({'case':{'seed':3001,'inputs':[]}}))
            audit=audit_consumption(data)
            self.assertIn(3001,audit['consumed_seeds']);self.assertEqual(audit['selected_fresh_seeds'],[3002,3003,3004,3005])

    def test_failed_run_inputs_still_consumed(self):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp)/'data';data.mkdir()
            (data/'manifest.json').write_text(json.dumps({'status':'failed','test_seeds':[3001]}))
            (data/'stable-3001.json.gz').write_bytes(b'persisted')
            self.assertIn(3001,audit_consumption(data)['consumed_seeds'])

    def test_prepared_plan_and_solver_offset_are_not_consumed_business(self):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp)/'data';data.mkdir()
            (data/'comparison-plan.json').write_text(json.dumps({'status':'prepared','fresh_business_seeds':[3001,3002,3003,3004]}))
            solver=data/'solver';solver.mkdir();(solver/'stable-high-4000000.json.gz').write_bytes(b'offset')
            audit=audit_consumption(data)
            self.assertNotIn(4000000,audit['consumed_seeds']);self.assertEqual(audit['selected_fresh_seeds'],self.seeds)

    def test_validation_does_not_generate_business_or_network(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            for name,value in (('comparison-plan.json',self.plan),('method-contract.json',self.contract),('seed-audit.json',self.plan['seed_audit'])):
                (p/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
            digest=hashlib.sha256((p/'comparison-plan.json').read_bytes()).hexdigest()
            (p/'manifest.json').write_text(json.dumps({'artifact_sha256':{'comparison-plan.json':digest}}))
            out=p/'unused'
            r=subprocess.run([sys.executable,str(ROOT/'experiments/run_network_comparison.py'),'--validate-only','--plan',str(p/'comparison-plan.json'),'--output',str(out)],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr);self.assertFalse(out.exists())
            self.assertFalse(json.loads(r.stdout)['inputs_generated'])

    def fixture(self,d):
        summaries=[]
        for c in self.plan['execution_order']:
            folder=d/c['name'];folder.mkdir()
            lo=1 if c['method']=='static' else .8
            hi=lo+.01
            row={'case':c['name'],'loss_lower':lo,'loss_upper':hi,'window_feasibility':'exceeds' if c['method']=='static' and c['scenario']=='switch' else 'within','api_bytes_per_s':0 if c['method']=='static' else 242}
            summaries.append(row)
            for name,value in (('packets.json',[{'packet_id':c['position'],'flow':1,'offset_ns':0,'ip_length':120,'path':[1,3]}]),('tasks.json',[]),('generation-bounds.json',{'loss_lower':lo,'loss_upper':hi}),('controller-visible-bounds.json',{'loss_lower':lo+2,'loss_upper':hi+2})):
                (folder/name).write_text(json.dumps(value))
            (folder/'collector.jsonl').write_text('')
        (d/'comparison-plan.json').write_text(json.dumps(self.plan))
        findings=summarize_pairs(d,self.plan['execution_order'],summaries)
        return findings,{r['case']:{k:str(v) for k,v in r.items()} for r in summaries}

    def test_complete_pairs_keep_budget_failures_and_business_denominator(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);f,base=self.fixture(d)
            self.assertEqual((f['paired_cases'],f['business_pair_groups'],f['deployment_repeat_groups'],f['both_within_pairs']),(224,112,128,168))
            self.assertGreater(check_pairs(d,self.plan['execution_order'],base),0)

    def test_forged_budget_pair_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);_,base=self.fixture(d);p=json.loads((d/'paired-cases.json').read_text());p[0]['both_within']=not p[0]['both_within'];(d/'paired-cases.json').write_text(json.dumps(p))
            with self.assertRaises(ValueError): check_pairs(d,self.plan['execution_order'],base)

    def test_missing_deployment_pair_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);_,base=self.fixture(d);p=json.loads((d/'paired-cases.json').read_text());p.pop();(d/'paired-cases.json').write_text(json.dumps(p))
            with self.assertRaises(ValueError): check_pairs(d,self.plan['execution_order'],base)

    def test_forged_deployment_variation_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);_,base=self.fixture(d);p=json.loads((d/'deployment-repeats.json').read_text());p[0]['loss_midpoint_range']=.5;(d/'deployment-repeats.json').write_text(json.dumps(p))
            with self.assertRaises(ValueError): check_pairs(d,self.plan['execution_order'],base)


if __name__=='__main__': unittest.main()
