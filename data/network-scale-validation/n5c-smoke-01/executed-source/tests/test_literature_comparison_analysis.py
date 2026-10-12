"""十方法空预设、完整配对、未知费用及部署分母边界。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
from test_observer_training_diagnostics import packet
from literature_comparison_identity import complete, summary_identity
from literature_comparison_pairs import summarize
from check_literature_comparison_analysis import check_pairs, check_integrals, typed_table, check_diagnostics
from analyze_literature_comparison import age_bounds

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    # 只读取冻结的身份清单。实际业务生成器不进入此合成检查。
    return json.loads((ROOT / 'data/literature-adaptation-plan/n4f-comparison-01/stage-plan.json').read_text())


def values(plan):
    result = []
    for e in plan['execution_order']:
        r = {k: e[k] for k in ('scenario', 'budget_level', 'seed', 'block', 'method', 'preset_index', 'parameter_index', 'position')}
        r.update(case=e['name'], controller_loss_lower=.8, controller_loss_upper=.81,
            collector_loss_lower=.5, collector_loss_upper=.51, window_feasibility='within',
            api_bytes_per_s=0 if e['method'] == 'static' else 300, local_notice_bytes_per_s=0)
        for k in ('e1', 'e2', 'e3', 'export'):
            r[k + '_lower_bytes_per_s'] = 100; r[k + '_upper_bytes_per_s'] = 101
        if e['method'] == 'phase_joint':
            r.update(controller_loss_lower=.7, controller_loss_upper=.71, collector_loss_lower=.6, collector_loss_upper=.61)
        result.append(r)
    return result


def csv_values(table):
    return [{k: '' if v is None else str(v) for k, v in r.items()} for r in table]


class LiteratureComparisonAnalysisTests(unittest.TestCase):
    def verify(self, mutate=None, failed=False, unknown=False):
        plan = fixture(); table = values(plan)
        if failed:
            for r in table:
                if r['method'] == 'static' and r['scenario'] == 'switch': r['window_feasibility'] = 'exceeds'
        if unknown:
            r = next(r for r in table if r['method'] == 'd_into_tt' and r['scenario'] == 'shared_priority')
            r.update(window_feasibility='unavailable', e3_lower_bytes_per_s=None, e3_upper_bytes_per_s=None)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder); (out / 'stage-plan.json').write_text(json.dumps(plan))
            for c in plan['execution_order']:
                d = out / c['name']; d.mkdir()
                (d / 'packets.json').write_text(json.dumps([packet(123)])); (d / 'tasks.json').write_text('[]')
                (d / 'collector.jsonl').write_text(json.dumps(dict(ether_type=0x88b6, packet_id=123, records=[dict(switch_id=1)])) + '\n')
                (d / 'decisions.jsonl').write_text(json.dumps(dict(scheduled_offset_us=0, skipped=False, selected={'config': [50000] * 4}, writes=[])) + '\n')
            findings = summarize(out, plan['execution_order'], table)
            self.assertEqual((findings['paired_cases'], findings['business_pair_groups'], findings['deployment_repeat_groups']), (288, 144, 160))
            self.assertEqual(findings['both_within_pairs'], 216 if failed else 287 if unknown else 288)
            if mutate: mutate(out, table)
            return check_pairs(out, plan['execution_order'], csv_values(table))

    def corrupt(self, filename, fn):
        def mutate(out, table):
            path = out / filename; data = json.loads(path.read_text()); fn(data); path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): self.verify(mutate)

    def test_complete_four_business_two_block_comparison(self): self.assertGreater(self.verify(), 12000)
    def test_unknown_fee_preserves_full_case(self): self.assertGreater(self.verify(unknown=True), 12000)
    def test_failed_static_preserves_all_pairs(self): self.assertGreater(self.verify(failed=True), 12000)
    def test_missing_pair_rejected(self): self.corrupt('paired-cases.json', lambda v: v.pop())
    def test_forged_gain_rejected(self): self.corrupt('paired-cases.json', lambda v: v[0].update(controller_midpoint_relative_reduction=.9))
    def test_duplicate_business_rejected(self): self.corrupt('business-pairs.json', lambda v: v.__setitem__(1, v[0]))
    def test_forged_deployment_variation_rejected(self): self.corrupt('deployment-repeats.json', lambda v: v[0].update(controller_midpoint_range=1))
    def test_deployments_do_not_enlarge_business_denominator(self): self.corrupt('paired-groups.json', lambda v: v[0].update(business_count=8))

    def test_diagnostic_entry_rejects_duplicate_matrix_before_batches(self):
        plan = fixture(); table = values(plan); table[1] = table[0].copy()
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out / 'findings.json').write_text('{}')
            (out / 'stage-plan.json').write_text(json.dumps(plan))
            with self.assertRaises(ValueError):
                check_diagnostics(out, plan['execution_order'], csv_values(table))

    def test_nullable_indices_are_bound_to_actual_methods(self):
        plan = fixture(); table = csv_values(values(plan))
        for r, c in zip(table, plan['execution_order']): self.assertEqual(summary_identity(r, c), 9)
        rows = typed_table(table)
        self.assertIsNone(next(r for r in rows if r['method'] == 'static')['parameter_index'])
        self.assertEqual(next(r for r in rows if r['method'] == 'intopp_due')['preset_index'], 4)

    def test_wrong_literature_preset_rejected(self):
        plan = fixture(); c = next(c for c in plan['execution_order'] if c['method'] == 'd_into_tt')
        r = csv_values([next(r for r in values(plan) if r['case'] == c['name'])])[0]; r['preset_index'] = ''
        with self.assertRaises(ValueError): summary_identity(r, c)

    def test_old_method_cannot_acquire_literature_preset(self):
        plan = fixture(); c = copy.deepcopy(next(c for c in plan['execution_order'] if c['method'] == 'local'))
        r = csv_values([next(r for r in values(plan) if r['case'] == c['name'])])[0]
        c['preset_index'] = 4; r['preset_index'] = '4'
        with self.assertRaises(ValueError): summary_identity(r, c)

    def test_duplicate_matrix_identity_rejected(self):
        plan = fixture(); rows = values(plan); rows[1] = rows[0]
        with self.assertRaises(ValueError): complete(rows, plan['business_seeds'])

    def test_two_observer_directions_remain_separate(self):
        def inspect(out, table):
            p = next(p for p in json.loads((out / 'paired-cases.json').read_text()) if p['method'] == 'phase_joint')
            self.assertEqual(p['controller_direction'], 'lower'); self.assertEqual(p['collector_direction'], 'higher')
        self.assertGreater(self.verify(inspect), 12000)

    def test_unknown_task_stays_in_age_denominator(self):
        tasks = [dict(object_id=dict(node=n, port=2, queue=0), start_us=0, end_us=1000000, target_us=1000000, weight=1) for n in (1, 2)]
        sample = dict(node=1, generation_lower_ns=0, generation_upper_ns=0, entered_lower_ns=100000000, entered_upper_ns=100000000)
        result = age_bounds(tasks, [sample], 0)
        self.assertAlmostEqual(result['loss_lower'], (.895 + 4) / 2)
        self.assertEqual(check_integrals(tasks, [sample], 0, result), 8)
