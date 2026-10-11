"""完整三块身份、控制器目标、逐部署预算与不生成业务的准备检查。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
from observer_confirmation_contract import ROOT, INDICES, LAYERS, expected_order, plan_fields, select, shortlist, validate


def fixture():
    plan = dict(plan_fields(), seed_audit={}, source_sha256={})
    values = []
    for entry in plan['execution_order']:
        upper = {'previous_common_initialization': 1., 'training_selected': .9, 'best_other_feasible': 1.1}[entry['role']]
        values.append(dict(entry, case=entry['name'], controller_loss_lower=upper-.01,
            controller_loss_upper=upper, collector_loss_lower=.1, collector_loss_upper=.2,
            window_feasibility='within', **{k+'_upper_bytes_per_s': 10 for k in LAYERS[:4]},
            api_bytes_per_s=0, local_notice_bytes_per_s=0))
    return plan, values


class ObserverConfirmationTests(unittest.TestCase):
    def test_fixed_shortlist_and_full_blocks(self):
        plan, _ = fixture(); validate(plan)
        self.assertEqual({g['scenario']: [c['candidate_index'] for c in g['candidates']] for g in shortlist()['groups']}, INDICES)
        entries = expected_order()
        self.assertEqual(len({e['name'] for e in entries}), 72)
        self.assertEqual([sum(e['block']==b for e in entries) for b in (1,2,3)], [24]*3)
        self.assertEqual({e['seed'] for e in entries}, {11,29})

    def test_changed_contract_or_candidate_rejected(self):
        for mutate in (lambda p: p.update(primary_observer='collector'), lambda p: p.update(deployment_blocks=2),
                       lambda p: p['execution_order'].pop(), lambda p: p['shortlist']['groups'][0]['candidates'][0].update(candidate_index=1),
                       lambda p: p['protocol']['budget']['low'].update(export=600)):
            plan, _ = fixture(); mutate(plan)
            with self.assertRaises(ValueError): validate(plan)

    def test_incomplete_source_set_rejected(self):
        plan, _ = fixture()
        with self.assertRaisesRegex(ValueError, '集合'): validate(plan, current=True)

    def test_full_selection_uses_controller_six_deployments(self):
        plan, values = fixture()
        scores, frozen = select(plan, values)
        self.assertEqual(len(scores), 12)
        self.assertTrue(all(s['case_count']==6 for s in scores))
        self.assertEqual([r['selected_candidate'] for r in frozen], [v[1] for v in INDICES.values()])
        self.assertTrue(all(r['status']=='frozen' for r in frozen))

    def test_one_deployment_failure_cannot_be_averaged_away(self):
        plan, values = fixture()
        for r in values:
            if r['role']=='training_selected' and r['seed']==29 and r['block']==3: r['window_feasibility']='exceeds'
        self.assertEqual([r['selected_candidate'] for r in select(plan, values)[1]], [v[0] for v in INDICES.values()])

    def test_minimax_precedes_average(self):
        plan, values = fixture()
        for r in values:
            if r['role']=='training_selected':
                v=1.2 if r['block']==3 and r['seed']==29 else .2
                r.update(controller_loss_upper=v, controller_loss_lower=v-.01)
        self.assertEqual([r['selected_candidate'] for r in select(plan, values)[1]], [v[0] for v in INDICES.values()])

    def test_missing_duplicate_or_wrong_block_rejected(self):
        for change in ('missing','duplicate','wrong_block'):
            plan, values = fixture()
            if change=='missing': values.pop()
            elif change=='duplicate': values[-1]=copy.deepcopy(values[0])
            else: values[0]['block']=4
            with self.assertRaises(ValueError): select(plan, values)

    def test_unknown_fee_or_nonfinite_age_not_selected(self):
        for field,value in (('export_upper_bytes_per_s',None),('controller_loss_upper',float('nan'))):
            plan, values = fixture()
            for row in values:
                if row['role']=='training_selected': row[field]=value
            self.assertEqual([r['selected_candidate'] for r in select(plan, values)[1]], [v[0] for v in INDICES.values()])

    def test_no_feasible_configuration_keeps_all_scores(self):
        plan, values = fixture()
        for row in values: row['window_feasibility']='ambiguous'
        scores, winners = select(plan, values)
        self.assertEqual(len(scores),12)
        self.assertTrue(all(w['status']=='finite_subset_infeasible' and w['selected_config'] is None for w in winners))

    def test_prepare_and_validate_only_generate_no_inputs(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name)
            command=[sys.executable,str(ROOT/'experiments/prepare_observer_confirmation.py'),'--output',str(folder/'plan')]
            prepared=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(prepared.returncode,0,prepared.stderr)
            result=subprocess.run([sys.executable,str(ROOT/'experiments/run_observer_confirmation.py'),
                '--plan',str(folder/'plan/confirmation-plan.json'),'--output',str(folder/'run'),'--validate-only'],
                cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['cases'],72)
            self.assertFalse((folder/'run').exists())
