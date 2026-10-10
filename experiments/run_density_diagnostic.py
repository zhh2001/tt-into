"""在两个事后指定子集补充密网格静态挑战者，不改变原控制器。"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from orchestration.rolling import Settings
from adaptive_workloads import generate
from rolling_rpc_cost import calibration
from rolling_workloads import MANAGEMENT, NODES, budget_for
from run_rolling_reference import SOURCES, plain, simulate, train, write_gzip, write_json

GRID = (50000, 100000, 150000, 200000, 400000, 600000, 800000, 1000000)
SCENARIOS = ('stable', 'shared_priority')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--primary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    primary = json.loads((args.primary / 'manifest.json').read_text())
    if primary['status'] != 'passed':
        raise ValueError('主实验未完成')
    args.output.mkdir(parents=True, exist_ok=False)
    run, settings = args.output, Settings(grid_us=GRID)
    sources = SOURCES + ('experiments/run_density_diagnostic.py', 'experiments/adaptive_workloads.py',
                         'experiments/check_rolling_reference.py')
    manifest = {'experiment_id': run.name, 'status': 'running', 'started_utc': datetime.now(timezone.utc).isoformat(),
                'kind': 'posthoc_static_grid_density', 'settings': settings, 'scenarios': SCENARIOS,
                'train_seeds': primary['train_seeds'], 'test_seeds': primary['test_seeds'], 'budgets': ['high'],
                'methods': ['static'], 'topology': {'nodes': NODES, 'routes': [{'flow': f, 'objects': r} for f, r in MANAGEMENT.routes.items()]},
                'budget_definition': {s: {'high': budget_for(s, 'high')} for s in SCENARIOS},
                'primary_manifest_sha256': hashlib.sha256((args.primary / 'manifest.json').read_bytes()).hexdigest(),
                'scope': '事后两个子集的密网格静态挑战，测试种子已消费；不是共同密网格盲测或一般最优',
                'configuration_space_asymmetry': '静态为八阈值加禁用，主实验动态仍为三阈值加禁用',
                'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}}
    for name in sources:
        target = run / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    write_json(run / 'manifest.json', manifest)
    try:
        write_json(run / 'rpc-calibration.json', calibration(ROOT))
        selections = []
        with gzip.open(run / 'training.jsonl.gz', 'wt') as stream:
            for scenario in SCENARIOS:
                selections.append(train([generate(scenario, seed) for seed in primary['train_seeds']],
                                        budget_for(scenario, 'high'), settings, stream, scenario, 'high'))
                print(json.dumps({'density_training_finished': scenario}), flush=True)
        write_json(run / 'training-selections.json', selections)
        (run / 'workloads').mkdir()
        (run / 'runs').mkdir()
        summary = []
        for scenario in SCENARIOS:
            trained = tuple(next(r['config'] for r in selections if r['scenario'] == scenario))
            for seed in primary['test_seeds']:
                workload = generate(scenario, seed)
                source = args.primary / 'workloads' / f'{scenario}-{seed}.json.gz'
                with gzip.open(source, 'rt') as stream:
                    expected = json.load(stream)
                if plain(workload) != expected:
                    raise ValueError('保留输入与主实验不一致')
                shutil.copyfile(source, run / 'workloads' / source.name)
                row, raw = simulate(workload, budget_for(scenario, 'high'), trained, 'static', settings)
                row['budget_level'] = 'high'
                summary.append(row)
                write_gzip(run / 'runs' / f'{scenario}-high-{seed}-static.json.gz', raw)
        write_json(run / 'summary.json', summary)
        manifest.update(status='passed', heldout_runs=len(summary), training_runs=2 * len(primary['train_seeds']) * 9**4)
    except Exception as error:
        manifest.update(status='failed', error=str(error))
        (run / 'failure.txt').write_text(traceback.format_exc())
        raise
    finally:
        manifest['finished_utc'] = datetime.now(timezone.utc).isoformat()
        manifest['artifact_sha256'] = {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in sorted(run.rglob('*')) if p.is_file() and p.name != 'manifest.json'
                                       and 'executed-source' not in p.parts}
        write_json(run / 'manifest.json', manifest)
    print(json.dumps({'status': manifest['status'], 'validation_runs': len(summary), 'training_runs': manifest['training_runs']}))


if __name__ == '__main__':
    main()
