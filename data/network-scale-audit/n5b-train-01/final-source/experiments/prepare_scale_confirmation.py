"""将双训练业务选中的有限静态候选冻结为三部署块确认输入。"""
import argparse
import copy
import json
from pathlib import Path
import random

from scalable_contract import sha
from check_observation_wire import load
from check_rolling_reference import same
from check_scale_static_delivery import check_selection


def prepare(analysis, output):
    contract = load(analysis / 'training-input.json')
    selected = load(analysis / 'selected-candidates.json')
    checks = check_selection(load(analysis / 'summary.json'), contract, selected,
                             load(analysis / 'candidate-scores.json'))
    same(len(selected), 10, '十个完整场景单元')
    same([len(c['selected']) for c in selected], [2] * 10, '各单元冻结两个候选')
    batches = []
    entries = []
    for block in range(3):
        networks = list(contract['networks'])
        random.Random(52603 + block).shuffle(networks)
        for network in networks:
            group = []
            for cell in selected:
                if cell['network_id'] != network:
                    continue
                for candidate in cell['selected']:
                    for seed in (11, 29):
                        original = next(e for e in contract['execution_order'] if
                            (e['network_id'], e['scenario'], e['candidate_id'], e['business_seed']) ==
                            (network, cell['scenario'], candidate['candidate_id'], seed))
                        entry = copy.deepcopy(original)
                        entry.update(block=block, training_rank=candidate['rank'],
                                     scope='已消费训练业务的静态部署重复确认')
                        group.append(entry)
            nodes = contract['networks'][network]['sampling_nodes']
            flows = len(contract['networks'][network]['flows'])
            random.Random(52603 + block * 1000 + nodes * 100 + flows).shuffle(group)
            same(len(group), 8, '每拓扑每块八例')
            for entry in group:
                entry['position'] = len(entries)
                entries.append(entry)
            batches.append(dict(batch_id=f'b{len(batches):02d}', block=block, network_id=network, entries=group))
    same(len(entries), 120, '完整确认分母')
    plan = dict(stage='n5b_static_confirmation', status='shortlist_prepared', execution_ready=False,
        analysis_manifest_sha256=sha(analysis / 'manifest.json'),
        selected_candidates_sha256=sha(analysis / 'selected-candidates.json'),
        training_input_sha256=sha(analysis / 'training-input.json'),
        independent_selection_checks=checks, expected_cases=120, blocks=[0, 1, 2],
        business_seeds=[11, 29], independent_test=False, order_seed=52603,
        networks=contract['networks'], execution_order=entries, batches=batches,
        capacity=1, duration_us=10000000, measurement_window_us=[2000000, 10000000],
        observation_period_us=100000, decision_period_us=1000000,
        confirmation_rule=dict(eligibility='每候选两个训练业务、三个部署块共六例全部工程通过且窗口within',
            ranking=['worst_controller_upper', 'mean_controller_upper', 'mean_export_bytes_per_s', 'config_lex_none_minus_one'],
            no_feasible_candidate='保留全部记录，该单元不冻结共同初始化',
            source_freeze='执行适配器验收后另建首例前prepared源码快照，继承本名单与顺序'))
    output.mkdir(parents=True, exist_ok=False)
    (output / 'confirmation-input.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    (output / 'selected-candidates.json').write_bytes((analysis / 'selected-candidates.json').read_bytes())
    record = dict(status='shortlist_prepared', execution_ready=False, business_generated=False,
        network_started=False, creator_sha256=sha(Path(__file__)),
        artifact_sha256={p.name: sha(p) for p in output.iterdir() if p.is_file()})
    (output / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    return dict(status='shortlist_prepared', cases=120, candidates=20, batches=15, execution_ready=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.analysis, args.output)))
