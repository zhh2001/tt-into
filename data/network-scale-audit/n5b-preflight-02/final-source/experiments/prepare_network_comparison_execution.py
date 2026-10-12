"""在生成业务前冻结消费审计、完整256例顺序及执行源码。"""
import argparse
import json
from pathlib import Path
import shutil

from network_comparison_contract import audit_consumption, expected_order, validate_plan, sha, METHOD_SHA256
from run_network_comparison import SOURCES, ROOT


def prepare(methods, output):
    record = json.loads((methods / 'manifest.json').read_text())
    if record['status'] != 'methods_frozen' or sha(methods / 'method-contract.json') != METHOD_SHA256:
        raise ValueError('方法合同尚未冻结或已改变')
    for name, digest in record['artifact_sha256'].items():
        if sha(methods / name) != digest: raise ValueError('方法合同产物来源不同')
    contract = json.loads((methods / 'method-contract.json').read_text())
    # 所有执行和分析文件必须先存在，审计与种子选择在它们准备之后执行。
    source_digests = {name: sha(ROOT / name) for name in SOURCES}
    audit = audit_consumption(ROOT / 'data')
    seeds = audit['selected_fresh_seeds']
    plan = {'status': 'prepared', 'execution_ready': True, 'expected_cases': 256,
        'business_count': 4, 'deployment_blocks': 2, 'fresh_inputs_generated': False,
        'fresh_business_seeds': seeds, 'order_seed': contract['order_seed'],
        'method_contract_sha256': METHOD_SHA256, 'method_contract': contract,
        'seed_audit': audit, 'source_sha256': source_digests,
        'execution_order': expected_order(contract, seeds),
        'scope': '完整比较清单先于业务生成，四个独立业务两部署块，全部失败保留，不再选择参数'}
    validate_plan(plan)
    output.mkdir(parents=True, exist_ok=False)
    def write(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    shutil.copyfile(methods / 'method-contract.json', output / 'method-contract.json')
    write('seed-audit.json', audit)
    write('comparison-plan.json', plan)
    write('execution-order.json', plan['execution_order'])
    for name in SOURCES:
        target = output / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    write('manifest.json', {'status': 'prepared', 'execution_ready': True,
        'method_contract_manifest_sha256': sha(methods / 'manifest.json'),
        'source_sha256': source_digests, 'fresh_business_seeds': seeds, 'fresh_inputs_generated': False,
        'artifact_sha256': {str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}})
    print(json.dumps({'status': 'prepared', 'expected_cases': 256, 'fresh_business_seeds': seeds,
                      'inputs_generated': False, 'execution_sources': len(SOURCES)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--methods', type=Path, default=ROOT / 'data/network-comparison-plan/n3h-methods-01')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.methods, args.output)
