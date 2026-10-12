"""八采样节点八流六方法正确性试点的执行前冻结。"""
from pathlib import Path
import argparse
import copy
import json
from scalable_contract import ROOT, sha, source_files
from orchestration.network_spec import FixedNetwork, fork_chain

REFERENCE = 'data/literature-adaptation-plan/n4f-comparison-01/method-contract.json'
METHODS = ('static', 'feedback', 'phase_joint', 'phase_guard', 'intopp_due', 'd_into_tt')
NETWORK = '8n8f'


def method_parameters():
    record = json.loads((ROOT / REFERENCE).read_text())
    result = {}
    for method in METHODS:
        native = method if method in record['reference']['methods'] else 'static'
        row = dict(adaptive=copy.deepcopy(record['reference']['methods'][native]['adaptive']))
        if method in record['literature_parameter_grids']:
            indices = record['selection']['selected_indices'][method]
            if len(indices) != 1: raise ValueError('全局文献参数未唯一冻结')
            row['literature'] = copy.deepcopy(record['literature_parameter_grids'][method][indices[0]])
        result[method] = row
    return result


def definition():
    spec = fork_chain(8, 8)
    params = method_parameters()
    entries = [dict(name=NETWORK+'-'+method, network_id=NETWORK, method=method, position=i,
        initial_config=[150000]*len(spec.keys), **params[method],
        budget=dict(link_bytes_per_s={v:4096 for v in spec.links.values()},
                    export_bytes_per_s=8192, control_bytes_per_s=4096),
        scope='八节点八流确定性正确性烟测，六方法共同入口，无性能排名')
        for i, method in enumerate(METHODS)]
    return dict(stage='n5b_maximum_preflight', status='prepared', networks={NETWORK:spec.record},
        execution_order=entries, expected_cases=6, business_seeds=[], independent_test=False,
        observation_period_us=100000, decision_period_us=1000000, capacity=1,
        duration_us=10000000, measurement_window_us=[2000000,10000000],
        work_motif=dict(period_us=125000,first_packet_us=10000,per_flow_phase_us=5000,manual_probes=[]),
        source_sha256={n:sha(ROOT/n) for n in source_files()},
        parameter_reference=dict(path=REFERENCE, sha256=sha(ROOT/REFERENCE)),
        cost_layers='八条采样出口带内、导出尝试、API序列化及本地通知有效负载分列',
        topology_scope='八采样节点与独立sink9、八并发流、56许可阈值配置位置')


def validate(plan, current=False):
    expected = definition()
    for key in expected.keys() - {'source_sha256'}:
        if plan[key] != expected[key]: raise ValueError('预声明试点字段改变 '+key)
    if not plan['source_sha256']: raise ValueError('执行源码摘要为空')
    if current:
        if plan['source_sha256'] != expected['source_sha256']: raise ValueError('冻结后执行源码改变')


def validate_paths(plan, output):
    for name, record in plan['networks'].items():
        for node in FixedNetwork(record).nodes:
            path=output.resolve()/('network-'+name)/f'node-{node}'/'notifications.ipc'
            if len(str(path).encode()) >= 108: raise ValueError('BMv2通知套接字路径过长')
    for entry in plan['execution_order']:
        if len(str(output.resolve()/f"notice-{entry['position']}.sock").encode()) >= 108:
            raise ValueError('需求通知套接字路径过长')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    out=p.parse_args().output
    plan=definition();validate(plan,True)
    out.mkdir(parents=True,exist_ok=False)
    (out/'stage-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps(dict(status='prepared',artifact_sha256={'stage-plan.json':sha(out/'stage-plan.json')}),indent=2)+'\n')
    print(json.dumps(dict(status='prepared',cases=6,packets=3840,inputs_generated=False,business_seeds=[])))
