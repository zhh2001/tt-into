"""最大配置六方法的线格式、合法来源及文献目标独立验收。"""
from check_scalable_network import check_case as check_network
from check_observation_wire import load, rows
from check_scale_literature_decisions import check_decisions


def check_case(directory, rpc):
    result = check_network(directory, rpc)
    case = load(directory / 'result.json')
    if case['method'] in ('intopp_due', 'd_into_tt'):
        extra = check_decisions(rows(directory / 'decisions.jsonl'), case,
                                load(directory / 'algorithm-settings.json'))
        result['checks'] += extra
        result['literature_checks'] = extra
    else:
        result['literature_checks'] = 0
    return result
