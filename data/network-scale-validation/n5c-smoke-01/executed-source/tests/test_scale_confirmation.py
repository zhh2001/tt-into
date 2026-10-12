"""确认入口的分块身份、六例资格、失败保留及独立排序检查。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'experiments')]
from scale_confirmation_execution import definition, validate, INPUT
from scale_confirmation_selection import select
from scale_confirmation_repeats import summarize
from check_scale_confirmation_delivery import check_selection


def fixture():
    plan = definition()
    chosen = json.loads((ROOT / INPUT).with_name('selected-candidates.json').read_text())
    rows = [dict(batch_id=b['batch_id'], case=e['name'], network_id=e['network_id'], scenario=e['scenario'],
        candidate_id=e['candidate_id'], business_seed=e['business_seed'], block=e['block'],
        status='passed', window_feasibility='within', controller_loss=[1, 1.2], collector_loss=[.5, .6],
        export_bytes_per_s=120) for b in plan['batches'] for e in b['entries']]
    return plan, chosen, rows


def frozen(cells, plan):
    return dict(status='frozen' if all(c['selected'] is not None for c in cells) else 'frozen_with_unavailable_cells',
        confirmation_input_sha256=plan['confirmation_input']['sha256'], selection_rule=plan['confirmation_rule'],
        methods=['static', 'feedback', 'phase_joint', 'phase_guard', 'intopp_due', 'd_into_tt'], cells=cells)


class ScaleConfirmationTests(unittest.TestCase):
    def test_original_order_and_all_fifteen_deployments_preserved(self):
        plan = definition()
        original = json.loads((ROOT / INPUT).read_text())
        self.assertEqual(plan['execution_order'], original['execution_order'])
        self.assertEqual(plan['batches'], original['batches'])
        self.assertEqual(len(plan['batches']), 15)

    def test_repeated_names_have_distinct_batch_identity(self):
        plan = definition('smoke')
        validate(plan, True)
        self.assertEqual(plan['execution_order'][0]['name'], plan['execution_order'][1]['name'])
        self.assertEqual([e['block'] for e in plan['execution_order']], [0, 1])
        self.assertEqual(plan['business_seeds'], [11])

    def test_per_batch_packet_identity_stays_in_ipv4_range(self):
        plan = definition()
        for batch in plan['batches']:
            flows = len(plan['networks'][batch['network_id']]['flows'])
            self.assertLess(7000 + flows * 80, 65536)

    def test_candidate_change_rejected(self):
        plan = definition()
        plan['execution_order'][0]['initial_config'][0] = 42
        with self.assertRaises(ValueError):
            validate(plan)

    def test_batch_block_change_rejected(self):
        plan = definition()
        plan['batches'][0]['block'] = 2
        with self.assertRaises(ValueError):
            validate(plan)

    def test_source_mismatch_rejected_before_business(self):
        plan = definition()
        key = next(iter(plan['source_sha256']))
        plan['source_sha256'][key] = '0' * 64
        with self.assertRaises(ValueError):
            validate(plan, True)

    def test_sixth_case_budget_failure_excludes_candidate(self):
        plan, chosen, rows = fixture()
        target = chosen[0]['selected'][0]['candidate_id']
        group = [r for r in rows if (r['network_id'], r['scenario'], r['candidate_id']) ==
                 (chosen[0]['network_id'], chosen[0]['scenario'], target)]
        group[-1]['window_feasibility'] = 'exceeds'
        cells, scores = select(rows, chosen)
        check_selection(rows, chosen, frozen(cells, plan), scores, plan)
        self.assertNotEqual(cells[0]['selected']['candidate_id'], target)

    def test_missing_deployment_rejected(self):
        _, chosen, rows = fixture()
        with self.assertRaises(ValueError):
            select(rows[:-1], chosen)

    def test_duplicate_block_cannot_replace_missing_block(self):
        _, chosen, rows = fixture()
        rows[-1]['block'] = 0
        with self.assertRaises(ValueError):
            select(rows, chosen)

    def test_worst_deployment_precedes_mean(self):
        plan, chosen, rows = fixture()
        first, second = [c['candidate_id'] for c in chosen[0]['selected']]
        for r in rows:
            if (r['network_id'], r['scenario']) == (chosen[0]['network_id'], chosen[0]['scenario']):
                r['controller_loss'] = [.1, .1] if r['candidate_id'] == first else [1, 1]
                if r['candidate_id'] == first and (r['block'], r['business_seed']) == (2, 29):
                    r['controller_loss'] = [1.5, 1.5]
        cells, scores = select(rows, chosen)
        self.assertEqual(cells[0]['selected']['candidate_id'], second)
        check_selection(rows, chosen, frozen(cells, plan), scores, plan)

    def test_independent_frozen_configuration_corruption_rejected(self):
        plan, chosen, rows = fixture()
        cells, scores = select(rows, chosen)
        result = copy.deepcopy(frozen(cells, plan))
        result['cells'][0]['selected']['config'][0] = 42
        with self.assertRaises(ValueError):
            check_selection(rows, chosen, result, scores, plan)

    def test_unavailable_cell_kept_without_blocking_other_cells(self):
        plan, chosen, rows = fixture()
        for r in rows:
            if (r['network_id'], r['scenario']) == (chosen[0]['network_id'], chosen[0]['scenario']):
                r.update(window_feasibility='unavailable', export_bytes_per_s=None)
        cells, scores = select(rows, chosen)
        self.assertIsNone(cells[0]['selected'])
        self.assertEqual(sum(c['selected'] is not None for c in cells), 9)
        self.assertEqual(len(scores), 20)
        check_selection(rows, chosen, frozen(cells, plan), scores, plan)

    def test_sampling_positions_ignore_batch_local_validation_id(self):
        _, _, cases = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            for c in cases:
                directory = output / c['batch_id'] / c['case']
                directory.mkdir(parents=True)
                pid = c['block'] * 1000
                (directory / 'packets.json').write_text(json.dumps([
                    dict(packet_id=pid, flow=1, offset_ns=1000000, path=[1])]))
                (directory / 'tasks.json').write_text('[]')
                (directory / 'collector.jsonl').write_text(json.dumps(dict(packet_id=pid,
                    ether_type=0x88b6, records=[dict(switch_id=1)])) + '\n')
            groups, totals = summarize(output, cases)
            self.assertEqual(len(groups), 40)
            self.assertEqual(totals['same_positions_groups'], 40)
            self.assertTrue(all(len(set(g['cases'])) == 3 for g in groups))


if __name__ == '__main__':
    unittest.main()
