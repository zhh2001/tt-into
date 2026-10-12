"""分批身份域、单区间任务及有限静态预算排序的独立验收。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'experiments')]
from scale_static_execution import definition,validate,INPUT
from scale_static_tasks import check_tasks
from scale_training_workload import inputs
from orchestration.network_spec import fork_chain
from analyze_scale_static import select
from check_scale_static_delivery import check_selection
from scale_static_bounds import output_frames


def scores_fixture():
    c=json.loads((ROOT/INPUT).read_text())
    rows=[dict(network_id=e['network_id'],scenario=e['scenario'],candidate_id=e['candidate_id'],business_seed=e['business_seed'],
        status='passed',window_feasibility='within',controller_loss=[1,1.2],export_bytes_per_s=120) for e in c['execution_order']]
    return c,rows


class ScaleStaticExecutionTests(unittest.TestCase):
    def test_batching_keeps_all_original_cases_in_exact_order(self):
        plan=definition();original=json.loads((ROOT/INPUT).read_text())
        self.assertEqual([e for b in plan['batches'] for e in b['entries']],original['execution_order'])
        self.assertEqual(len(plan['batches']),20)
        self.assertTrue(all(len(b['entries'])==48 for b in plan['batches']))

    def test_every_batch_has_valid_capture_id_space(self):
        plan=definition()
        for batch in plan['batches']:
            flows=len(plan['networks'][batch['network_id']]['flows'])
            self.assertLess((len(batch['entries'])-1)*1000+flows*80,65536)

    def test_candidate_mutation_rejected(self):
        p=definition();p['batches'][0]['entries'][0]['initial_config'][0]=123
        with self.assertRaises(ValueError):validate(p)

    def test_smoke_boundary_cases_no_new_test_seed(self):
        p=definition('smoke');validate(p,True)
        self.assertEqual(len(p['execution_order']),3)
        self.assertTrue(all(e['business_seed']==11 for e in p['execution_order']))
        self.assertTrue(all(v is None for v in p['execution_order'][1]['initial_config']))

    def test_single_interval_accepted_for_all_sampling_nodes(self):
        spec=fork_chain(8,2);_,notice,tasks=inputs(spec,'shared_priority',11)
        self.assertEqual(check_tasks(spec.record,'shared_priority',tasks,notice),9)

    def test_duplicate_carrier_task_rejected(self):
        spec=fork_chain(5,8);_,notice,tasks=inputs(spec,'stable',11)
        tasks.append(copy.deepcopy(tasks[0]))
        with self.assertRaises(ValueError):check_tasks(spec.record,'stable',tasks,notice)

    def test_target_corruption_rejected(self):
        spec=fork_chain(3,2);_,notice,tasks=inputs(spec,'shared_priority',11);tasks[-1]['target_us']=500000
        with self.assertRaises(ValueError):check_tasks(spec.record,'shared_priority',tasks,notice)

    def test_unannounced_extra_demand_rejected(self):
        spec=fork_chain(3,2);_,notice,tasks=inputs(spec,'stable',11);notice.append(copy.deepcopy(notice[0]))
        with self.assertRaises(ValueError):check_tasks(spec.record,'stable',tasks,notice)

    def test_rank_requires_both_training_budgets(self):
        contract,rows=scores_fixture()
        for r in rows:
            if (r['network_id'],r['scenario'],r['candidate_id'])==('n3f2','stable','C00'):
                r['controller_loss']=[.1,.1]
                if r['business_seed']==29:r['window_feasibility']='exceeds'
        chosen,scores=select(rows,contract);check_selection(rows,contract,chosen,scores)
        selected=next(c for c in chosen if (c['network_id'],c['scenario'])==('n3f2','stable'))
        self.assertNotIn('C00',[r['candidate_id'] for r in selected['selected']])

    def test_worst_business_before_average(self):
        contract,rows=scores_fixture()
        for r in rows:
            if r['network_id']=='n3f2' and r['scenario']=='stable':
                r['controller_loss']=[3,3]
                if r['candidate_id']=='C00':r['controller_loss']=[.1,1.5 if r['business_seed']==29 else .1]
                if r['candidate_id']=='C01':r['controller_loss']=[1,1]
        chosen,scores=select(rows,contract);check_selection(rows,contract,chosen,scores)
        selected=next(c for c in chosen if (c['network_id'],c['scenario'])==('n3f2','stable'))
        self.assertEqual(selected['selected'][0]['candidate_id'],'C01')

    def test_independent_selected_score_corruption_rejected(self):
        c,rs=scores_fixture();chosen,scores=select(rs,c);chosen[0]['selected'][0]['mean_controller_upper']=0
        with self.assertRaises(ValueError):check_selection(rs,c,chosen,scores)

    def test_empty_export_capture_with_no_attempts_is_legal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);case=dict(name='off',network_id='n3f2',topology=dict(sampling_nodes=3,sink=4),report_count=0)
            d=root/'off';d.mkdir();(d/'packets.json').write_text('[]')
            d=root/'network-n3f2'/'node-4';d.mkdir(parents=True);(d/'st-eth3_out.pcap').write_bytes(b'')
            self.assertEqual(output_frames(root,[case]),{'off':[]})

    def test_missing_expected_export_capture_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);case=dict(name='on',network_id='n3f2',topology=dict(sampling_nodes=3,sink=4),report_count=1)
            d=root/'on';d.mkdir();(d/'packets.json').write_text('[]')
            d=root/'network-n3f2'/'node-4';d.mkdir(parents=True);(d/'st-eth3_out.pcap').write_bytes(b'')
            with self.assertRaises(ValueError):output_frames(root,[case])


if __name__=='__main__':unittest.main()
