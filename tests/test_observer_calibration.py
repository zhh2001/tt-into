"""固定全局参数分母、合法预算与控制器目标优先级。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'experiments'))
from observer_calibration_contract import ROOT,LAYERS,plan_fields,select,validate


def fixture():
    plan=dict(plan_fields(),source_sha256={})
    values=[dict(e,case=e['name'],controller_loss_lower=.5,controller_loss_upper=.51,
        collector_loss_lower=.1,collector_loss_upper=.11,successful_updates=1,
        window_feasibility='within', **{k+'_upper_bytes_per_s':10 for k in LAYERS[:4]},
        api_bytes_per_s=10,local_notice_bytes_per_s=10) for e in plan['execution_order']]
    return plan,values


class ObserverCalibrationTests(unittest.TestCase):
    def test_complete_global_matrix(self):
        plan,values=fixture(); validate(plan)
        self.assertEqual(len(values),184); self.assertEqual(len(plan['presets']),23)
        self.assertEqual({e['seed'] for e in values},{1301,1423})
        scores,frozen=select(plan,values)
        self.assertEqual(len(scores),18); self.assertTrue(all(s['case_count']==8 for s in scores))
        self.assertEqual([r['selected_parameter'] for r in frozen],[0,0,0])

    def test_controller_objective_not_collector(self):
        plan,values=fixture()
        for r in values:
            if r['parameter_index']==4: r.update(controller_loss_lower=.2,controller_loss_upper=.21,collector_loss_upper=3.9)
        self.assertEqual([r['selected_parameter'] for r in select(plan,values)[1]],[4]*3)

    def test_budget_failure_precedes_lower_age(self):
        plan,values=fixture()
        for r in values:
            if r['parameter_index']==0: r.update(window_feasibility='exceeds',controller_loss_lower=.01,controller_loss_upper=.02)
        self.assertEqual([r['selected_parameter'] for r in select(plan,values)[1]],[1]*3)

    def test_one_failure_preserved_and_not_averaged(self):
        plan,values=fixture()
        for method in ('uniform_guard','phase_guard','phase_local_guard'):
            next(r for r in values if r['method']==method and r['parameter_index']==0)['window_feasibility']='exceeds'
        scores,winners=select(plan,values)
        self.assertEqual([r['selected_parameter'] for r in winners],[1]*3)
        self.assertEqual([s['rank'][0] for s in scores if s['parameter_index']==0],[1]*3)

    def test_normalized_excess_precedes_age_if_all_fail(self):
        plan,values=fixture()
        for r in values:
            r['window_feasibility']='exceeds'; r['export_upper_bytes_per_s']=1800
            if r['parameter_index']==5: r['export_upper_bytes_per_s']=1000
        scores,winners=select(plan,values)
        self.assertEqual([w['selected_parameter'] for w in winners],[5]*3)
        self.assertTrue(all(w['status']=='frozen_with_budget_failures' for w in winners))

    def test_updates_then_parameter_index_tiebreak(self):
        plan,values=fixture()
        for r in values:
            if r['parameter_index']==3: r['successful_updates']=0
        self.assertEqual([r['selected_parameter'] for r in select(plan,values)[1]],[3]*3)

    def test_missing_duplicate_and_foreign_identity_rejected(self):
        for mode in ('missing','duplicate','foreign'):
            plan,values=fixture()
            if mode=='missing': values.pop()
            elif mode=='duplicate': values[-1]=copy.deepcopy(values[0])
            else: values[0]['seed']=3005
            with self.assertRaises(ValueError): select(plan,values)

    def test_unknown_fees_and_nonfinite_age_not_selected(self):
        for field,value in (('api_bytes_per_s',None),('controller_loss_upper',float('nan'))):
            plan,values=fixture()
            for r in values:
                if r['parameter_index']==0: r[field]=value
            self.assertEqual([r['selected_parameter'] for r in select(plan,values)[1]],[1]*3)

    def test_changed_initialization_observer_budget_or_presets_rejected(self):
        for change in (lambda p:p.update(primary_observer='collector'),lambda p:p.update(observation_period_us=1000000),
                       lambda p:p['common_initialization'][0]['selected_config'].__setitem__(0,50000),
                       lambda p:p['protocol']['budget']['low'].update(export=600),lambda p:p['presets'].pop()):
            plan,_=fixture(); change(plan)
            with self.assertRaises(ValueError): validate(plan)
        plan,_=fixture()
        with self.assertRaisesRegex(ValueError,'集合'): validate(plan,current=True)

    def test_prepare_validate_only_generate_no_business(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            p=subprocess.run([sys.executable,str(ROOT/'experiments/prepare_observer_calibration.py'),
                '--output',str(folder/'plan')],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            p=subprocess.run([sys.executable,str(ROOT/'experiments/run_observer_calibration.py'),
                '--plan',str(folder/'plan/calibration-plan.json'),'--output',str(folder/'run'),'--validate-only'],
                cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(json.loads(p.stdout)['cases'],184)
            self.assertFalse((folder/'run').exists()); self.assertFalse(list((folder/'plan').rglob('packets.json')))
