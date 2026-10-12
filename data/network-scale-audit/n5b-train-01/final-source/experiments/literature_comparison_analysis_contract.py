"""业务生成前冻结十方法分析源码，保持既有执行计划不变。"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from literature_comparison_execution_contract import ROOT, sha, validate
from literature_comparison_identity import METHODS

SOURCES = ('experiments/analyze_literature_comparison.py',
    'experiments/check_literature_comparison_analysis.py', 'experiments/literature_comparison_pairs.py',
    'experiments/literature_comparison_identity.py', 'experiments/literature_comparison_analysis_contract.py',
    'experiments/plot_literature_comparison.py', 'experiments/check_literature_comparison_delivery.py',
    'experiments/diagnose_observer_training.py', 'experiments/network_entry_bounds.py',
    'experiments/observation_diagnostics.py', 'experiments/check_rolling_reference.py',
    'experiments/observer_comparison_contract.py', 'tests/test_literature_comparison_analysis.py')


def validate_contract(path, plan_path, current=False):
    record = json.loads(path.read_text())
    plan = json.loads(plan_path.read_text())
    validate(plan)
    expected = dict(status='prepared', execution_plan_sha256=sha(plan_path), expected_cases=320,
        methods=list(METHODS), business_seeds=plan['business_seeds'], independent_business_units=4,
        deployment_blocks=2, paired_cases=288, business_pair_groups=144, deployment_repeat_groups=160,
        scenario_method_groups=36, method_aggregate_groups=40, primary_observer='controller',
        secondary_observer='collector')
    if plan['stage'] != 'comparison' or plan['protocol']['performance']['methods'] != list(METHODS):
        raise ValueError('分析只接受冻结的十方法独立比较')
    if any(record.get(k) != v for k, v in expected.items()): raise ValueError('分析阶段合同不符')
    if set(record['analysis_source_sha256']) != set(SOURCES) or record['unit_test_count'] < 498:
        raise ValueError('分析源码集合或回归分母不符')
    if current:
        for name, digest in record['analysis_source_sha256'].items():
            if sha(ROOT / name) != digest: raise ValueError('分析冻结后源码改变: ' + name)
        manifest = json.loads((path.parent / 'manifest.json').read_text())
        for name, digest in manifest['artifact_sha256'].items():
            if sha(path.parent / name) != digest: raise ValueError('分析准备附件改变')
    return record


def prepare(plan_path, output):
    plan = json.loads(plan_path.read_text()); validate(plan, current=True)
    if plan['stage'] != 'comparison': raise ValueError('需要十方法比较清单')
    output.mkdir(parents=True, exist_ok=False)
    discovery = ('import unittest; loader=unittest.TestLoader(); '
                 'suite=loader.discover("tests"); '
                 'assert not loader.errors, loader.errors; print(suite.countTestCases())')
    count = int(subprocess.check_output([sys.executable, '-c', discovery], cwd=ROOT, text=True))
    record = dict(status='prepared', execution_plan_sha256=sha(plan_path), expected_cases=320,
        methods=list(METHODS), business_seeds=plan['business_seeds'], independent_business_units=4,
        deployment_blocks=2, paired_cases=288, business_pair_groups=144, deployment_repeat_groups=160,
        scenario_method_groups=36, method_aggregate_groups=40, primary_observer='controller',
        secondary_observer='collector', unit_test_count=count,
        analysis_source_sha256={name: sha(ROOT / name) for name in SOURCES})
    write = lambda name, value: (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    write('analysis-contract.json', record)
    for name in SOURCES:
        target = output / 'executed-source' / name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    write('manifest.json', dict(status='prepared', fresh_inputs_generated=False,
        artifact_sha256={str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}))
    validate_contract(output / 'analysis-contract.json', plan_path, current=True)
    print(json.dumps(dict(status='prepared', analysis_sources=len(SOURCES), unit_test_count=count, cases=320)))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); prepare(a.plan, a.output)
