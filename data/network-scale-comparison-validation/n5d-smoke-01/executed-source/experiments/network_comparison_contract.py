"""独立业务比较的固定方法、完整配对身份与历史消费审计。"""
import json
import hashlib
import os
from pathlib import Path
import random
import re

from check_matched_network import sha
from prepare_network_comparison import METHODS

ORDER_SEED = 42426407
METHOD_SHA256 = '9999635fcabf01f802e8c088a476e60960153ee4abf567b79b9e8c467c45e24e'


def expected_order(contract, seeds):
    if len(seeds) != 4 or len(set(seeds)) != 4 or any(type(s) is not int or s < 3001 for s in seeds):
        raise ValueError('独立业务分母必须为四个不同的新业务')
    result = []
    rng = random.Random(ORDER_SEED)
    for seed in seeds:
        for block in (1, 2):
            group = []
            for profile in contract['profiles']:
                for method in METHODS:
                    config = contract['methods'][method]
                    group.append(dict(scenario=profile['scenario'], budget_level=profile['budget_level'],
                        initial_config=profile['initial_config'], method=method, seed=seed, block=block,
                        adaptive=config['adaptive'], parameter_index=config['parameter_index'],
                        name=f"{profile['scenario']}-{profile['budget_level']}-s{seed}-b{block}-{method}"))
            rng.shuffle(group)
            result.extend(group)
    return [dict(r, position=i, order_seed=ORDER_SEED) for i, r in enumerate(result)]


def audit_consumption(data):
    consumed, declared, reserved, files, payloads = set(), set(), set(), [], []
    def visit(value, destination):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.startswith('reserved'):
                    visit(item, reserved)
                    if isinstance(item, list): reserved.update(x for x in item if type(x) is int)
                    continue
                if key in ('seed', 'seeds', 'random_seed') or key.endswith('_seeds'):
                    destination.update(x for x in (item if isinstance(item, list) else [item]) if type(x) is int)
                if key not in ('source_sha256', 'artifact_sha256', 'raw_artifact_sha256', 'execution_order'):
                    visit(item, destination)
        elif isinstance(value, list):
            if destination is reserved:
                reserved.update(x for x in value if type(x) is int)
            for item in value: visit(item, destination)
    for folder, dirs, names in os.walk(data):
        dirs[:] = sorted(d for d in dirs if d not in ('executed-source', 'executed-analysis-source', '__pycache__'))
        for name in sorted(names):
            p = Path(folder) / name
            if name == 'manifest.json' or name.endswith('-plan.json') or name == 'method-contract.json':
                value = json.loads(p.read_text())
                before = set(declared)
                visit(value, reserved if value.get('status') in ('prepared','methods_frozen') else declared)
                if value.get('status') in ('passed', 'complete', 'completed'):
                    visit(value, consumed)
                # 未成功或运行中的案例仍由实际输入独立证明消费。
                files.append({'path': str(p.relative_to(data.parent)), 'sha256': sha(p),
                              'status': value.get('status'), 'declared_seeds': sorted(declared - before)})
            elif name in ('frozen-inputs.json', 'generation-attempts.json'):
                value = json.loads(p.read_text())
                visit(value, consumed)
                payloads.append({'path': str(p.relative_to(data.parent)), 'sha256': sha(p), 'kind': 'generated_inputs'})
            elif name.endswith('.json.gz') and p.parent.name != 'solver':
                match = re.match(r'^[a-z_]+(?:-(?:high|low))?-(\d+)(?:-|\.json)', name)
                if match:
                    consumed.add(int(match[1]))
                    payloads.append({'path': str(p.relative_to(data.parent)), 'kind': 'persisted_workload_or_replay', 'seed': int(match[1])})
    # 未知是否执行的历史声明保守排除，纯 reserved 不据此算已消费。
    excluded = consumed | declared
    seeds, candidate = [], 3001
    while len(seeds) < 4:
        if candidate not in excluded: seeds.append(candidate)
        candidate += 1
    return {'status': 'audited', 'historical_manifests': files, 'generated_payloads': payloads,
        'consumed_seeds': sorted(consumed), 'historical_declared_seeds': sorted(declared),
        'reserved_seeds': sorted(reserved), 'excluded_seeds': sorted(excluded), 'selected_fresh_seeds': seeds,
        'fresh_inputs_generated': False,
        'scope': '扫描全部本地 data 清单、已冻结业务输入和已保存压缩业务/回放；纯预留分列，历史未知执行声明保守排除，不把排序种子当业务样本'}


def validate_plan(plan, directory=None, check_current=False):
    contract = plan['method_contract']
    if hashlib.sha256((json.dumps(contract, ensure_ascii=False, indent=2) + '\n').encode()).hexdigest() != METHOD_SHA256:
        raise ValueError('方法参数、初始化或预算改变')
    if plan['method_contract_sha256'] != METHOD_SHA256:
        raise ValueError('冻结方法合同摘要不同')
    if (plan['status'], plan['execution_ready'], plan['expected_cases'], plan['business_count'],
        plan['deployment_blocks'], plan['fresh_inputs_generated'], plan['order_seed']) != ('prepared', True, 256, 4, 2, False, ORDER_SEED):
        raise ValueError('独立比较完整分母或状态不同')
    if contract['status'] != 'methods_frozen' or contract['execution_ready'] or list(contract['methods']) != list(METHODS):
        raise ValueError('比较方法合同不符')
    if plan['fresh_business_seeds'] != plan['seed_audit']['selected_fresh_seeds'] or set(plan['fresh_business_seeds']) & set(plan['seed_audit']['excluded_seeds']):
        raise ValueError('新业务已经消费或与审计不符')
    if plan['execution_order'] != expected_order(contract, plan['fresh_business_seeds']):
        raise ValueError('比较完整矩阵、身份、参数或顺序不同')
    if directory is not None:
        directory = Path(directory)
        if sha(directory / 'method-contract.json') != METHOD_SHA256 or json.loads((directory / 'method-contract.json').read_text()) != contract:
            raise ValueError('方法合同来源不同')
        if json.loads((directory / 'seed-audit.json').read_text()) != plan['seed_audit']:
            raise ValueError('业务消费审计来源不同')
        for name, digest in plan['source_sha256'].items():
            if sha(directory / 'executed-source' / name) != digest:
                raise ValueError('执行源码快照不同')
            if check_current and sha(Path(__file__).resolve().parents[1] / name) != digest:
                raise ValueError('冻结后执行源码发生变化')
