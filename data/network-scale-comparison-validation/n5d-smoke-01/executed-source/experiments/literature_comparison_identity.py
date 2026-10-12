"""十方法完整矩阵及可空预设的实际身份核对。"""
from collections import Counter

METHODS = ('static', 'feedback', 'local', 'joint', 'phase_joint', 'uniform_guard',
           'phase_guard', 'phase_local_guard', 'intopp_due', 'd_into_tt')
PROFILES = (('stable', 'high'), ('shared_priority', 'high'), ('switch', 'low'), ('bursty_jitter', 'low'))
KEYS = ('scenario', 'budget_level', 'seed', 'block', 'method')


def complete(table, seeds):
    if len(seeds) != 4 or len(set(seeds)) != 4:
        raise ValueError('比较需要四条独立业务')
    expected = Counter((s, b, seed, block, method) for s, b in PROFILES
                       for seed in seeds for block in (1, 2) for method in METHODS)
    actual = Counter(tuple(r[k] for k in KEYS) for r in table)
    if actual != expected:
        raise ValueError('十方法、场景、业务及部署的完整身份不符')


def summary_identity(summary, case):
    for key in ('scenario', 'budget_level', 'method'):
        if summary[key] != case[key]: raise ValueError('汇总身份不符: ' + key)
    if summary['case'] != case['name']: raise ValueError('实际案例名不符')
    for key in ('seed', 'block', 'position', 'preset_index', 'parameter_index'):
        value = None if summary[key] in ('', None) else int(summary[key])
        if value != case[key]: raise ValueError('实际配置索引或业务不符: ' + key)
    if case['method'] in ('intopp_due', 'd_into_tt'):
        if type(case['preset_index']) is not int or case['parameter_index'] != case['preset_index']:
            raise ValueError('文献预设必须明确且与实际参数一致')
    elif case['preset_index'] is not None:
        raise ValueError('旧方法不得附加文献预设')
    if case['method'] == 'static' and case['parameter_index'] is not None:
        raise ValueError('静态不接受动态参数编号')
    return 9
