"""冻结文献适配工程输入，不生成新的业务。"""
import argparse
import json
from pathlib import Path

from literature_contract import ROOT, ORDER_SEED, method_contract, order, sha, validate
from run_literature_pilot import SOURCES


def prepare(output):
    contract = method_contract()
    output.mkdir(parents=True, exist_ok=False)
    write = lambda name, value: (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    write('method-contract.json', contract)
    plan = dict(status='prepared', stage='literature_tt_correctness', business_seed=11,
        independent_test=False, observation_period_us=100000, decision_period_us=1000000,
        duration_us=10000000, warmup_us=[0, 2000000], measurement_window_us=[2000000, 10000000],
        order_seed=ORDER_SEED, expected_cases=8, expected_observation_slots=100, expected_decision_slots=10,
        method_contract=contract, method_contract_sha256=sha(output / 'method-contract.json'),
        execution_order=order(contract), source_sha256={name: sha(ROOT / name) for name in SOURCES})
    validate(plan, current=True)
    write('observer-plan.json', plan)
    write('manifest.json', dict(status='prepared', business_generated=False,
        artifact_sha256={name: sha(output / name) for name in ('observer-plan.json', 'method-contract.json')}))
    print(json.dumps(dict(status='prepared', cases=8, business_generated=False)))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    prepare(p.parse_args().output)
