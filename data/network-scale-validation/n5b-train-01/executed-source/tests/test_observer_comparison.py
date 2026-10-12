"""验证新业务消费边界、完整两块矩阵和共同方法合同。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'experiments'))
from observer_comparison_contract import ROOT, plan_fields, validate, audit_consumption


def fixture():
    audit=dict(status='audited',excluded_seeds=[3001,3002,3003,3004],selected_fresh_seeds=[3005,3006,3007,3008])
    return dict(plan_fields(audit),source_sha256={})


class ObserverComparisonTests(unittest.TestCase):
    def test_full_two_independent_blocks(self):
        plan=fixture(); validate(plan)
        order=plan['execution_order']
        self.assertEqual(len(order),256); self.assertEqual(len({r['name'] for r in order}),256)
        self.assertEqual([r['block'] for r in order],[1]*128+[2]*128)
        self.assertEqual({r['seed'] for r in order},{3005,3006,3007,3008})
        for scenario in ('stable','shared_priority','switch','bursty_jitter'):
            self.assertEqual(sum(r['scenario']==scenario for r in order),64)

    def test_global_parameter_and_initialization_consistency(self):
        plan=fixture()
        for method,index in (('uniform_guard',0),('phase_guard',2),('phase_local_guard',5)):
            members=[r for r in plan['execution_order'] if r['method']==method]
            self.assertEqual(len(members),32)
            self.assertEqual({r['parameter_index'] for r in members},{index})
            self.assertTrue(all(r['adaptive']==members[0]['adaptive'] for r in members))
        for scenario in ('stable','shared_priority','switch','bursty_jitter'):
            configs={tuple(r['initial_config']) for r in plan['execution_order'] if r['scenario']==scenario}
            self.assertEqual(len(configs),1)

    def test_missing_duplicate_wrong_block_and_order_rejected(self):
        for mutate in (lambda p:p['execution_order'].pop(),
            lambda p:p['execution_order'].__setitem__(1,copy.deepcopy(p['execution_order'][0])),
            lambda p:p['execution_order'][0].update(block=2),
            lambda p:p['execution_order'].reverse()):
            plan=fixture(); mutate(plan)
            with self.assertRaises(ValueError): validate(plan)

    def test_consumed_and_nonminimal_seeds_rejected(self):
        plan=fixture(); plan['seed_audit']['excluded_seeds'].append(3005)
        with self.assertRaises(ValueError): validate(plan)
        plan=fixture(); plan['fresh_business_seeds']=[3010,3011,3012,3013]
        plan['seed_audit']['selected_fresh_seeds']=plan['fresh_business_seeds']
        with self.assertRaises(ValueError): validate(plan)

    def test_changed_parameters_observer_budget_and_source_set_rejected(self):
        for mutate in (lambda p:p['execution_order'][0]['adaptive'].update(gain_margin=.9),
            lambda p:p.update(primary_observer='collector'),lambda p:p.update(observation_period_us=1000000),
            lambda p:p['protocol']['budget']['low'].update(export=900)):
            plan=copy.deepcopy(fixture()); mutate(plan)
            with self.assertRaises(ValueError): validate(plan)
        with self.assertRaisesRegex(ValueError,'集合'): validate(fixture(),current=True)

    def test_prepared_reservation_is_not_consumption(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'manifest.json').write_text(json.dumps(dict(status='prepared',fresh_business_seeds=[3005])))
            self.assertEqual(audit_consumption(root)['selected_fresh_seeds'],[3001,3002,3003,3004])

    def test_failed_generated_business_is_consumed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'manifest.json').write_text(json.dumps(dict(status='failed',fresh_business_seeds=[3001])))
            (root/'frozen-inputs.json').write_text(json.dumps({'case':{'seed':3001,'inputs':[[],[],[]]}}))
            audit=audit_consumption(root)
            self.assertIn(3001,audit['consumed_seeds']); self.assertEqual(audit['selected_fresh_seeds'],[3002,3003,3004,3005])

    def test_prepare_and_validate_do_not_generate_or_start(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            result=subprocess.run([sys.executable,str(ROOT/'experiments/prepare_observer_comparison_execution.py'),
                '--output',str(folder/'plan')],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([sys.executable,str(ROOT/'experiments/run_observer_comparison.py'),
                '--plan',str(folder/'plan/comparison-plan.json'),'--output',str(folder/'run'),'--validate-only'],
                cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['cases'],256)
            self.assertFalse((folder/'run').exists()); self.assertFalse(list((folder/'plan').rglob('packets.json')))
