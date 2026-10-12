"""仅依据已冻结模型训练，预声明下一轮有限网络静态挑战候选。"""
import argparse
import gzip
import hashlib
import itertools
import json
from pathlib import Path
import random
import shutil


PROFILES = (('stable', 'high'), ('shared_priority', 'high'), ('switch', 'low'), ('bursty_jitter', 'low'))
GRID = (None, 50000, 100000, 150000, 200000, 400000, 600000, 800000, 1000000)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code(config):
    return tuple(0 if value is None else value for value in config)


def shortlist(scored, initial, random_seed):
    ranked = sorted(scored, key=lambda config: scored[config])
    if ranked[0] != tuple(initial):
        raise ValueError('冻结配置与独立模型训练排序不符')
    selected = {}
    def add(config, reason):
        selected.setdefault(tuple(config), reason)
    add((None,) * 4, 'all_disabled_reference')
    add((50000,) * 4, 'all_minimum_threshold_reference')
    for config in [c for c in ranked if c not in selected][:8]:
        add(config, 'model_top_eight_remaining')
    neighbors = []
    for config in ranked:
        changes = [i for i in range(4) if config[i] != initial[i]]
        if len(changes) == 1 and abs(GRID.index(config[changes[0]]) - GRID.index(initial[changes[0]])) == 1:
            neighbors.append(config)
    for config in [c for c in neighbors if c not in selected][:6]:
        add(config, 'adjacent_to_frozen_static')
    remainder = [c for c in itertools.product(GRID, repeat=4) if c not in selected]
    for config in random.Random(random_seed).sample(remainder, 24 - len(selected)):
        add(config, 'uniform_grid_sample')
    if len(selected) != 24 or tuple(initial) not in selected:
        raise ValueError('静态候选数或冻结参照缺失')
    return [{'candidate_index': i, 'config': list(config), 'reason': reason, 'model_score': scored[config]}
            for i, (config, reason) in enumerate(selected.items())]


def prepare(reference, output):
    manifest = json.loads((reference / 'manifest.json').read_text())
    if manifest['status'] != 'passed' or manifest['train_seeds'] != [11, 29]:
        raise ValueError('训练范围或状态不符')
    selections = json.loads((reference / 'training-selections.json').read_text())
    used, groups = {}, []
    for index, (scenario, level) in enumerate(PROFILES):
        inputs = []
        for seed in (11, 29):
            name = f'training/{scenario}-{seed}.jsonl.gz'
            digest = sha(reference / name)
            if digest != manifest['artifact_sha256'][name]:
                raise ValueError('原始训练摘要错误')
            used[name] = digest
            with gzip.open(reference / name, 'rt') as stream:
                inputs.append([json.loads(line) for line in stream])
        if any(len(values) != 6561 for values in inputs):
            raise ValueError('静态训练分母错误')
        scored = {}
        for a, b in zip(*inputs):
            if a['config'] != b['config'] or (a['scenario'], b['scenario'], a['seed'], b['seed']) != (scenario, scenario, 11, 29):
                raise ValueError('训练配对错位')
            config = tuple(a['config'])
            if config in scored or any(v not in GRID for v in config) or len(config) != 4:
                raise ValueError('训练配置重复或越界')
            scored[config] = (max(a['budget_scores'][level], b['budget_scores'][level]),
                              (a['loss'] + b['loss']) / 2, sum(v is not None for v in config), code(config))
        initial = next(r['config'] for r in selections if (r['scenario'], r['budget_level']) == (scenario, level))
        seed = 41421356 + index
        groups.append({'scenario': scenario, 'budget_level': level, 'sampling_seed': seed,
                       'frozen_static': initial, 'candidates': shortlist(scored, initial, seed)})
    output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve()
    target = output / 'executed-source/experiments' / source.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    plan = {'status': 'prepared', 'scope': '仅模型训练输入的有限网络静态候选，尚无新网络评分或性能结果',
            'reference_manifest_sha256': sha(reference / 'manifest.json'), 'input_sha256': used,
            'training_selection_sha256': sha(reference / 'training-selections.json'),
            'source_sha256': {'experiments/' + source.name: sha(source)},
            'training_seeds': [11, 29], 'grid_us': list(GRID), 'configuration_count': 6561,
            'candidates_per_cell': 24, 'expected_network_cases': 192,
            'network_repetitions_per_candidate_seed': 1, 'groups': groups,
            'selection_rule': '所有训练案例费用上界达标后，最小化两训练种子中最坏年龄损失上界；并列按均值、启用项数、配置编码',
            'failure_rule': '预算失败、不确定窗口和不可评价年龄全部保留；没有共同达标候选则报告不可行而不选失败配置',
            'limits': '有限网格子集，不是全网格最优或已完成网络充分调参；新测试种子未参与'}
    (output / 'candidate-plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': plan['status'], 'cells': len(groups), 'candidates_per_cell': 24,
                      'expected_network_cases': 192}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.reference, args.output)
