"""规模完整身份、新业务隔离及独立配对检查。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'experiments')]
from scale_comparison_execution import definition,validate
from scale_comparison_pairs import records,complete
from check_scale_comparison_pairs import check
from check_scale_comparison_literature import check_decisions


def audit():
    return dict(selected_fresh_seeds=[3017,3018,3019,3020],excluded_seeds=[11,29,*range(3001,3017)])


def fixture():
    plan=definition('comparison',audit());summary=[];sig={};ds={}
    for batch in plan['batches']:
        for e in batch['entries']:
            s={k:e[k] for k in ('network_id','scenario','business_seed','block','method')}
            s.update(batch_id=batch['batch_id'],case=e['name'],window_feasibility='within',controller_loss=[1,1.2],collector_loss=[.5,.6],
                fee_rates={'export':[100,100],'api':[0,0]},weighted_active_interval_coverage=1.0)
            summary.append(s);name=s['batch_id']+'/'+s['case'];sig[name]=[[1,1000000,1]];ds[name]=[]
    return plan,summary,sig,ds


class ScaleComparisonTests(unittest.TestCase):
    def test_sparse_eight_node_history_retains_full_path_links(self):
        d=ROOT/'data/network-scale-comparison-failures/n5d-interrupted-01/b00/n8f2-stable-s3017-d_into_tt'
        case=json.loads((d/'result.json').read_text())
        decisions=[json.loads(v) for v in (d/'decisions.jsonl').read_text().splitlines()]
        opts=json.loads((d/'algorithm-settings.json').read_text())
        self.assertEqual(case['status'],'failed')
        self.assertEqual(check_decisions(decisions,case,opts),13560)

    def test_full_matrix_and_ten_independent_deployments(self):
        plan=definition('comparison',audit());validate(plan,True)
        self.assertEqual(plan['expected_cases'],432);self.assertEqual(len(plan['batches']),10)
        self.assertEqual(sorted(len(b['entries']) for b in plan['batches']),[24,24,*([48]*8)])

    def test_unconfirmed_cell_is_not_added_back(self):
        plan=definition('comparison',audit())
        self.assertFalse(any((e['network_id'],e['scenario'])==('n5f2','stable') for e in plan['execution_order']))

    def test_consumed_test_seed_rejected(self):
        a=audit();a['selected_fresh_seeds'][0]=3001
        with self.assertRaises(ValueError):definition('comparison',a)

    def test_initial_configuration_change_rejected(self):
        p=definition('comparison',audit());p['execution_order'][0]['initial_config'][0]=42
        with self.assertRaises(ValueError):validate(p)

    def test_method_parameter_change_rejected(self):
        p=definition('comparison',audit());p['execution_order'][0]['adaptive']['reserve_fraction']=.7
        with self.assertRaises(ValueError):validate(p)

    def test_duplicate_block_rejected(self):
        p,s,_,_=fixture();s[-1]['block']=1-s[-1]['block']
        with self.assertRaises(ValueError):complete(s,p)

    def test_source_change_rejected_before_business(self):
        p=definition('comparison',audit());p['source_sha256'][next(iter(p['source_sha256']))]='0'*64
        with self.assertRaises(ValueError):validate(p,True)

    def test_batch_local_ids_fit_with_eight_flows(self):
        p=definition('comparison',audit())
        for b in p['batches']:
            f=len(p['networks'][b['network_id']]['flows']);self.assertLess((len(b['entries'])-1)*1000+f*80,65536)

    def test_smoke_uses_consumed_business_and_three_connections(self):
        p=definition('smoke',audit());validate(p,True)
        self.assertEqual(p['business_seeds'],[11]);self.assertFalse(p['independent_test'])
        self.assertEqual({e['method'] for e in p['execution_order']},{'static','phase_joint','d_into_tt'})

    def test_all_pairs_and_business_denominators(self):
        p,s,sig,ds=fixture();a,b,r,g=records(s,sig,ds,p)
        self.assertEqual((len(a),len(b),len(r),len(g)),(360,180,216,45))

    def test_fee_unavailability_kept_in_pair_and_group(self):
        p,s,sig,ds=fixture();r=next(x for x in s if x['method']=='phase_joint');r.update(window_feasibility='unavailable',fee_rates={'export':[None,None],'api':[100,100]})
        a,b,z,g=records(s,sig,ds,p);pair=next(x for x in a if x['dynamic_case']==r['batch_id']+'/'+r['case'])
        self.assertFalse(pair['both_within']);self.assertEqual(pair['dynamic_fee_rates']['export'],[None,None])
        group=next(x for x in g if (x['network_id'],x['scenario'],x['method'])==(r['network_id'],r['scenario'],r['method']))
        self.assertEqual(group['dynamic_mean_fee_rates']['export'],[None,None]);self.assertEqual(group['both_within_pairs'],7)

    def test_independent_pair_checker_rejects_changed_age(self):
        p,s,sig,ds=fixture();a,b,r,g=records(s,sig,ds,p)
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            artifacts={'summary.json':s,'stage-plan.json':p,'sample-signatures.json':sig,'decision-signatures.json':ds,'paired-cases.json':a,'business-pairs.json':b,'deployment-repeats.json':r,'paired-groups.json':g}
            for name,value in artifacts.items():(d/name).write_text(json.dumps(value))
            for row in s:
                case=d/row['batch_id']/row['case'];case.mkdir(parents=True)
                (case/'packets.json').write_text(json.dumps([dict(packet_id=0,flow=1,offset_ns=1000000)]))
                (case/'collector.jsonl').write_text(json.dumps(dict(packet_id=0,ether_type=0x88b6,records=[dict(switch_id=1)]))+'\n')
                (case/'decisions.jsonl').write_text('')
            self.assertGreater(check(d),1000)
            a[0]['controller_difference_bounds'][0]=42;(d/'paired-cases.json').write_text(json.dumps(a))
            with self.assertRaises(ValueError):check(d)


if __name__=='__main__':unittest.main()
