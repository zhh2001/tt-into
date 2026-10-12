"""绑定既有静态确认名单，冻结真实分块执行源码。"""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path

from scalable_contract import ROOT, sha, source_files
from scale_preflight_contract import validate_paths

INPUT = 'data/network-scale-plan/n5b-confirmation-01/confirmation-input.json'
TRAINING = 'data/network-scale-plan/n5b-static-training-01/training-plan.json'
ANALYSIS = 'data/network-scale-validation/n5b-train-01'


def shortlist():
    path = ROOT / INPUT
    record = json.loads((path.parent / 'manifest.json').read_text())
    for name, digest in record['artifact_sha256'].items():
        if sha(path.parent / name) != digest:
            raise ValueError('确认名单产物改变 ' + name)
    plan = json.loads(path.read_text())
    if (plan['status'], plan['execution_ready'], plan['expected_cases'], plan['blocks'],
            plan['business_seeds'], plan['independent_test']) != ('shortlist_prepared', False, 120, [0, 1, 2], [11, 29], False):
        raise ValueError('确认名单角色或分母改变')
    for name, field in [('manifest.json', 'analysis_manifest_sha256'),
                        ('selected-candidates.json', 'selected_candidates_sha256'),
                        ('training-input.json', 'training_input_sha256')]:
        if sha(ROOT / ANALYSIS / name) != plan[field]:
            raise ValueError('训练选择来源改变 ' + name)
    if sha(ROOT / TRAINING) != plan['training_input_sha256']:
        raise ValueError('原训练输入改变')
    counts = Counter((e['network_id'], e['scenario'], e['candidate_id']) for e in plan['execution_order'])
    if len(counts) != 20 or set(counts.values()) != {6}:
        raise ValueError('每候选六例确认分母不完整')
    if [e for b in plan['batches'] for e in b['entries']] != plan['execution_order']:
        raise ValueError('确认分批与完整顺序不一致')
    return plan


def definition(mode='confirmation'):
    frozen = shortlist()
    entries = copy.deepcopy(frozen['execution_order'])
    batches = copy.deepcopy(frozen['batches'])
    if mode == 'smoke':
        # 两个部署块重复同名案例，专门验收分块身份及重建入口。
        entries = [next(e for e in entries if (e['block'], e['network_id'], e['scenario'],
                   e['candidate_id'], e['business_seed']) == (block, 'n3f2', 'stable', 'C21', 11))
                   for block in (0, 1)]
        batches = []
        for position, entry in enumerate(entries):
            entry['confirmation_position'] = entry['position']
            entry['position'] = position
            entry['scope'] = '确认入口同名跨块烟测，非候选性能选择'
            batches.append(dict(batch_id=f'b{position:02d}', block=entry['block'],
                                network_id=entry['network_id'], entries=[entry]))
    elif mode != 'confirmation':
        raise ValueError('未知确认执行模式')
    return dict(stage='n5b_static_confirmation_execution', status='prepared', execution_ready=True,
        mode=mode, confirmation_input=dict(path=INPUT, sha256=sha(ROOT / INPUT)),
        training_input=dict(path=TRAINING, sha256=sha(ROOT / TRAINING)),
        expected_cases=len(entries), execution_order=entries, batches=batches,
        networks=frozen['networks'], business_seeds=[11] if mode == 'smoke' else [11, 29],
        independent_test=False, blocks=[0, 1] if mode == 'smoke' else [0, 1, 2],
        observation_period_us=100000, decision_period_us=1000000, capacity=1,
        duration_us=10000000, measurement_window_us=[2000000, 10000000],
        confirmation_rule=frozen['confirmation_rule'],
        batch_contract=dict(packet_id_base_per_case=1000, max_cases=8,
            validation_identity_domain='逐批IPv4 ID仅用于抓包核对，在线身份仍为FlowTag/Session'),
        source_sha256={n: sha(ROOT / n) for n in source_files()})


def validate(plan, current=False, output=None):
    expected = definition(plan['mode'])
    for key in expected.keys() - {'source_sha256'}:
        if plan.get(key) != expected[key]:
            raise ValueError('确认执行冻结字段改变 ' + key)
    if current and plan['source_sha256'] != expected['source_sha256']:
        raise ValueError('确认源码冻结后改变')
    if output is not None:
        for batch in plan['batches']:
            validate_paths(dict(networks={batch['network_id']: plan['networks'][batch['network_id']]},
                                execution_order=batch['entries']), output / batch['batch_id'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('confirmation', 'smoke'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plan = definition(args.mode)
    validate(plan, True)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'stage-plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    for source, name in [(INPUT, 'confirmation-input.json'), (TRAINING, 'training-input.json')]:
        (args.output / name).write_bytes((ROOT / source).read_bytes())
    record = dict(status='prepared', execution_ready=True, business_generated=False, network_started=False,
        artifact_sha256={p.name: sha(p) for p in args.output.iterdir() if p.is_file()})
    (args.output / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(dict(status='prepared', cases=plan['expected_cases'], batches=len(plan['batches']))))
