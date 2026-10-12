"""重放一个已消费的归档案例，核对确定性结果，不重新声明盲测。"""
import argparse
import copy
import json
from pathlib import Path

from run_matched_reference import (AdaptiveSettings, GUARDED, METHODS, PARAMETERS, matched_management,
                                   plain, sha, simulate, workload, write_gzip, write_json)
from check_rolling_reference import load


def without_timing(raw):
    r = copy.deepcopy(raw)
    r['summary'].pop('decision_elapsed_ns')
    for d in r['decisions']:
        d.pop('decision_elapsed_ns')
    return r


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--scenario', required=True)
    p.add_argument('--budget', choices=('low', 'high'), required=True)
    p.add_argument('--seed', type=int, required=True)
    p.add_argument('--method', choices=METHODS, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    m = load(a.run / 'manifest.json')
    root = Path(__file__).resolve().parents[1]
    if m['status'] != 'passed' or a.scenario not in m['scenarios'] or a.seed not in m['test_seeds']:
        p.error('仅支持已完成清单中的保留案例重放')
    for name, digest in m['source_sha256'].items():
        if sha(root / name) != digest:
            raise ValueError('当前依赖源码与归档执行版本不同：' + name)
    w = workload(a.scenario, a.seed)
    if plain(w) != load(a.run / 'workloads' / f'{a.scenario}-{a.seed}.json.gz'):
        raise ValueError('再生成业务与归档不一致')
    s = next(r for r in load(a.run / 'training-selections.json') if (r['scenario'], r['budget_level']) == (a.scenario, a.budget))
    frozen = load(a.run / 'frozen-parameters.json')
    params = PARAMETERS[frozen['selected_indices'][a.method]] if a.method in GUARDED else AdaptiveSettings()
    from rolling_workloads import budget_for
    row, raw = simulate(w, budget_for(a.scenario, a.budget), tuple(s['config']), a.method,
                        params, matched_management(load(a.run / 'rpc-reference.json')))
    row['budget_level'] = a.budget
    name = f'{a.scenario}-{a.budget}-{a.seed}-{a.method}.json.gz'
    original = load(a.run / 'runs' / name)
    if without_timing(plain(raw)) != without_timing(original):
        raise ValueError('重放的确定性事件、策略或费用结果与归档不一致')
    a.output.mkdir(parents=True, exist_ok=False)
    write_gzip(a.output / name, raw)
    write_json(a.output / 'manifest.json', {'status': 'passed', 'kind': 'consumed_case_replay',
        'original_manifest_sha256': sha(a.run / 'manifest.json'), 'source_sha256': m['source_sha256'],
        'artifact_sha256': {name: sha(a.output / name)}, 'scope': '全部确定性内容一致，宿主求解耗时不比较，不是新保留样本'})
    print(json.dumps({'status': 'passed', 'case': name}, ensure_ascii=False))


if __name__ == '__main__':
    main()
