"""保持方法和完整矩阵，冻结支持无视图跳过周期的新验收入口。"""
from literature_stage_contract import ROOT, sha, validate as original_validate


def validate(plan, current=False):
    expected = original_validate(plan)
    if plan['stage'] != 'comparison': raise ValueError('本入口仅用于十方法独立比较')
    if current:
        from run_literature_comparison import SOURCES
        if set(plan['source_sha256']) != set(SOURCES): raise ValueError('新执行源码集合不完整')
        for name, digest in plan['source_sha256'].items():
            if sha(ROOT / name) != digest: raise ValueError('新执行冻结后源码改变: ' + name)
        from network_comparison_contract import audit_consumption
        audit = audit_consumption(ROOT / 'data')
        if set(plan['business_seeds']) & set(audit['excluded_seeds']): raise ValueError('新业务已经消费')
    return expected
