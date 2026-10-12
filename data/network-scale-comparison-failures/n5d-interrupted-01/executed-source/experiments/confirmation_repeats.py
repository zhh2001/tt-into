"""同业务三块采样位置与候选配对诊断，不解释为统计置信区间。"""
import json

from diagnose_static_transfer import load, signature


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def summarize_repeats(output, cases):
    signatures, workloads = {}, {}
    for c in cases:
        workload, positions = signature(output / c['name'])
        key = (c['scenario'], c['seed'])
        full = (workload, load(output / c['name'] / 'tasks.json'))
        if key in workloads and workloads[key] != full:
            raise ValueError('静态确认未使用同业务与任务')
        workloads[key] = full
        signatures[c['name']] = positions
    write(output / 'sample-signatures.json', signatures)
    seeds = []
    for s, b, candidate, seed in sorted({(c['scenario'], c['budget_level'], c['candidate_index'], c['seed']) for c in cases}):
        rs = sorted([c for c in cases if (c['scenario'], c['budget_level'], c['candidate_index'], c['seed']) == (s, b, candidate, seed)], key=lambda c: c['block'])
        if [c['block'] for c in rs] != [1, 2, 3]:
            raise ValueError('三块部署重复缺失')
        bounds = [[load(output / c['name'] / 'generation-bounds.json')['loss_' + k] for k in ('lower', 'upper')] for c in rs]
        mids = [(lo + hi) / 2 for lo, hi in bounds]
        positions = [signatures[c['name']] for c in rs]
        seeds.append(dict(scenario=s, budget_level=b, candidate_index=candidate, seed=seed,
            cases=[c['name'] for c in rs], sample_counts=[len(p) for p in positions],
            same_sampling_positions=positions[0] == positions[1] == positions[2],
            differences_from_first=[len(set(map(tuple, positions[0])) ^ set(map(tuple, p))) for p in positions],
            loss_bounds=bounds, loss_midpoint_range=max(mids) - min(mids),
            loss_intervals_common_overlap=max(lo for lo, _ in bounds) <= min(hi for _, hi in bounds)))
    pairs = []
    cells = load(output / 'confirmation-plan.json')['unique_config_cells']
    for s, b in sorted({(c['scenario'], c['budget_level']) for c in cells}):
        members = [c for c in cells if (c['scenario'], c['budget_level']) == (s, b)]
        if len(members) == 1:
            continue  # 同一配置的两个角色只算一个实验组。
        model = next(c for c in members if 'frozen_model_reference' in c['roles'])
        alternative = next(c for c in members if 'network_selected' in c['roles'])
        for seed in (11, 29):
            for block in (1, 2, 3):
                names = [next(c['name'] for c in cases if (c['scenario'], c['seed'], c['block'], c['candidate_index']) ==
                         (s, seed, block, cell['candidate_index'])) for cell in (model, alternative)]
                positions = [signatures[n] for n in names]
                bounds = [[load(output / n / 'generation-bounds.json')['loss_' + k] for k in ('lower', 'upper')] for n in names]
                pairs.append(dict(scenario=s, budget_level=b, seed=seed, block=block,
                    model_case=names[0], alternative_case=names[1],
                    same_sampling_positions=positions[0] == positions[1],
                    position_symmetric_difference=len(set(map(tuple, positions[0])) ^ set(map(tuple, positions[1]))),
                    model_loss_bounds=bounds[0], alternative_loss_bounds=bounds[1],
                    loss_intervals_overlap=max(lo for lo, _ in bounds) <= min(hi for _, hi in bounds),
                    alternative_minus_model_bounds=[bounds[1][0] - bounds[0][1], bounds[1][1] - bounds[0][0]]))
    write(output / 'seed-repeat-diagnostics.json', seeds)
    write(output / 'candidate-repeat-pairs.json', pairs)
    return dict(seed_config_groups=len(seeds), unchanged_sampling_groups=sum(r['same_sampling_positions'] for r in seeds),
        common_age_overlap_groups=sum(r['loss_intervals_common_overlap'] for r in seeds), candidate_pairs=len(pairs),
        same_sampling_pairs=sum(r['same_sampling_positions'] for r in pairs), overlapping_age_pairs=sum(r['loss_intervals_overlap'] for r in pairs),
        max_within_business_midpoint_range=max(r['loss_midpoint_range'] for r in seeds),
        scope='同一业务重复与候选配对，生成年龄条件区间并非置信区间，无一般等价结论')
