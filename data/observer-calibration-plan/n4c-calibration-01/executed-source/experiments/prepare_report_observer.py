"""冻结已消费种子11的共同报告观察工程试点，不生成新业务。"""
import argparse
import json
from pathlib import Path
import shutil

from report_observer_contract import METHOD_PATH, METHOD_SHA256, ROOT, ORDER_SEED, order, sha, validate
from run_report_observer import SOURCES


def prepare(output):
    contract = json.loads(METHOD_PATH.read_text())
    plan = {'status': 'prepared', 'stage': 'report_observer_correctness', 'business_seed': 11,
            'independent_test': False, 'observation_period_us': 100000, 'decision_period_us': 1000000,
            'duration_us': 10000000, 'warmup_us': [0, 2000000], 'measurement_window_us': [2000000, 10000000],
            'order_seed': ORDER_SEED, 'expected_cases': 8, 'expected_observation_slots': 100,
            'expected_decision_slots': 10, 'method_contract_sha256': METHOD_SHA256,
            'execution_order': order(contract), 'source_sha256': {n: sha(ROOT / n) for n in SOURCES},
            'scope': '已消费训练业务正确性，非未见测试、性能排名、硬周期或硬预算'}
    validate(plan, current=True)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'observer-plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    shutil.copyfile(METHOD_PATH, output / 'method-contract.json')
    (output / 'manifest.json').write_text(json.dumps({'status': 'prepared', 'business_generated': False,
        'artifact_sha256': {n: sha(output / n) for n in ('observer-plan.json', 'method-contract.json')}}, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'prepared', 'cases': 8, 'business_generated': False}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output)
