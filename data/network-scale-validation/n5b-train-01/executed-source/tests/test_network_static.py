"""空报告、双业务选择、来源篡改及完整静态训练顺序边界。"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'experiments'))
from network_static_contract import execution_order, validate_plan, select_candidates
from check_network_calibration import check_prefixes
from network_entry_bounds import generation_bounds
from check_rolling_reference import clipped_loss
from check_static_analysis import check_selection


class StaticTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT/'data/network-static-plan/n3e-candidates-01/candidate-plan.json').read_text())

    def summaries(self):
        return [{'scenario': g['scenario'], 'budget_level': g['budget_level'], 'candidate_index': c['candidate_index'],
                 'seed': seed, 'window_feasibility': 'within', 'loss_lower': 1., 'loss_upper': 1.}
                for g in self.plan['groups'] for c in g['candidates'] for seed in (11, 29)]

    def test_complete_order_and_seed_identity(self):
        order = execution_order(self.plan)
        self.assertEqual(len(order), 192)
        self.assertEqual(len({r['name'] for r in order}), 192)
        self.assertEqual(order, execution_order(self.plan))
        for block in range(1, 9):
            rows = [r for r in order if r['block'] == block]
            self.assertEqual(sorted(r['candidate_index'] for r in rows), list(range(24)))
            self.assertEqual(len({(r['scenario'],r['budget_level'],r['seed']) for r in rows}), 1)

    def test_smoke_does_not_change_full_order(self):
        smoke = execution_order(self.plan, True)
        self.assertEqual({r['candidate_index'] for r in smoke}, {0,1})
        self.assertEqual({r['seed'] for r in smoke}, {11})
        self.assertEqual(smoke, [r for r in execution_order(self.plan) if r in smoke])

    def test_duplicate_candidate_rejected(self):
        self.plan['groups'][0]['candidates'][2]['config'] = [None]*4
        with self.assertRaises(ValueError):
            validate_plan(self.plan)

    def test_anchor_removed_rejected(self):
        self.plan['groups'][0]['candidates'][0]['config'] = [1000000]*4
        with self.assertRaises(ValueError):
            validate_plan(self.plan)

    def test_source_digest_tamper_rejected(self):
        self.plan['source_sha256']['experiments/prepare_network_static.py'] = '0'*64
        with self.assertRaisesRegex(ValueError, '源码'):
            validate_plan(self.plan, ROOT/'data/network-static-plan/n3e-candidates-01')

    def test_both_seeds_required(self):
        rs = self.summaries()
        with self.assertRaisesRegex(ValueError, '分母'):
            select_candidates(self.plan, rs[:-1])

    def test_one_seed_budget_failure_excludes_candidate(self):
        for status in ('exceeds', 'ambiguous', 'unavailable'):
            rs = self.summaries()
            rs[0]['window_feasibility'] = status
            scores, _ = select_candidates(self.plan, rs)
            self.assertFalse(scores[0]['feasible'])

    def test_worst_upper_precedes_mean(self):
        rs = self.summaries()
        rs[0]['loss_lower'], rs[0]['loss_upper'] = 0., .1
        rs[1]['loss_lower'], rs[1]['loss_upper'] = 0., 1.1
        _, selected = select_candidates(self.plan, rs)
        self.assertNotEqual(selected[0]['selected_candidate'], 0)

    def test_nan_age_excluded(self):
        rs = self.summaries()
        rs[0]['loss_upper'] = float('nan')
        scores, _ = select_candidates(self.plan, rs)
        self.assertFalse(scores[0]['age_evaluable'])

    def test_no_feasible_candidate_is_reported(self):
        rs = self.summaries()
        for r in rs:
            r['window_feasibility'] = 'exceeds'
        _, selected = select_candidates(self.plan, rs)
        self.assertTrue(all(r['status'] == 'finite_subset_infeasible' and r['selected_config'] is None for r in selected))

    def test_unknown_empty_age_keeps_all_tasks(self):
        tasks = [{'object_id': {'node': n, 'port': 2, 'queue': 0}, 'start_us': 2000000,
                  'end_us': 10000000, 'weight': n, 'target_us': 100000} for n in (1,2,3)]
        loss, denominator = clipped_loss(tasks, [], 4)
        self.assertEqual(loss, 4)
        self.assertEqual(denominator, 48000000)
        with tempfile.TemporaryDirectory() as name:
            d = Path(name)
            for file in ('send.jsonl', 'destination.jsonl', 'collector.jsonl'):
                (d/file).write_text('')
            (d/'tasks.json').write_text(json.dumps(tasks))
            age = generation_bounds(d, {'origin_monotonic_ns':0})
            self.assertEqual([age['loss_lower'],age['loss_upper']], [4,4])
            self.assertEqual(age['weighted_active_interval_coverage'],0)
            self.assertTrue(all(c['compatible'] is None for c in age['local_clock_offset'].values()))

    def test_empty_prefix_is_valid_but_forged_cache_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            d = Path(name)
            for file in ('collector.jsonl', 'notice-send.jsonl', 'notices.jsonl'):
                (d/file).write_text('')
            (d/'notice-schedule.json').write_text('[]')
            (d/'result.json').write_text('{"origin_monotonic_ns":0}')
            e = {'read_started_ns':1,'read_finished_ns':2,'ingest_started_ns':3,'available_ns':4,'cutoff_ns':5,
                 'prefix_bytes':0,'file_bytes':0,'row_count':0,'reports_before':0,'reports_after':0,
                 'prefix_sha256':hashlib.sha256(b'').hexdigest()}
            decision = {'cycle_started_ns':0,'cutoff_ns':5,'solver_started_ns':6,'availability':e,
                        'view':{'reports':[],'cache':[]}}
            (d/'availability.jsonl').write_text(json.dumps(e)+'\n')
            (d/'decisions.jsonl').write_text(json.dumps(decision)+'\n')
            self.assertGreater(check_prefixes(d),0)
            decision['view']['cache'] = [{'object_id': {'node':1,'port':2,'queue':0}}]
            (d/'decisions.jsonl').write_text(json.dumps(decision)+'\n')
            with self.assertRaises(ValueError):
                check_prefixes(d)

    def selection_fixture(self, d):
        rs = self.summaries()
        scores, selected = select_candidates(self.plan, rs)
        for file, value in (('candidate-plan.json',self.plan), ('candidate-scores.json',scores), ('network-selections.json',selected)):
            (d/file).write_text(json.dumps(value))
        base = {str(i):{k:str(v) for k,v in r.items()} for i,r in enumerate(rs)}
        return base

    def test_independent_selection_checker_accepts_complete_pairs(self):
        with tempfile.TemporaryDirectory() as name:
            d = Path(name)
            self.assertEqual(check_selection(d,self.selection_fixture(d)),388)

    def test_independent_checker_rejects_forged_winner(self):
        with tempfile.TemporaryDirectory() as name:
            d = Path(name)
            base = self.selection_fixture(d)
            selected = json.loads((d/'network-selections.json').read_text())
            selected[0]['selected_candidate'] = 1
            (d/'network-selections.json').write_text(json.dumps(selected))
            with self.assertRaises(ValueError):
                check_selection(d,base)


if __name__ == '__main__':
    unittest.main()
