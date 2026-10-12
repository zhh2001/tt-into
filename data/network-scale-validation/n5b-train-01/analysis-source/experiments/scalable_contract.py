"""N5a确定性正确性试点的执行前合同。"""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from orchestration.network_spec import FixedNetwork, fork_chain


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files():
    return sorted(str(p.relative_to(ROOT)) for directory, suffixes in
        [('src', ('.py', '.p4')), ('experiments', ('.py',)), ('tests', ('.py',))]
        for p in (ROOT / directory).rglob('*') if p.is_file() and p.suffix in suffixes)


def definition():
    methods = json.loads((ROOT / 'data/observer-comparison-plan/n4c-methods-01/method-contract.json').read_text())['methods']
    networks = {'legacy-3n-2f': fork_chain(3, 2, True).record, 'expanded-5n-4f': fork_chain(5, 4).record}
    entries = []
    for network_id, topology in networks.items():
        spec = FixedNetwork(topology)
        for method in ('static', 'phase_joint', 'phase_guard', 'manual'):
            entries.append(dict(name=network_id + '-' + method, network_id=network_id, method=method,
                position=len(entries), initial_config=[150000, 400000, 200000, 200000] if network_id.startswith('legacy') else [150000] * len(spec.keys),
                adaptive=methods['phase_guard' if method == 'manual' else method]['adaptive'],
                budget=dict(link_bytes_per_s={v: 4096 for v in spec.links.values()}, export_bytes_per_s=8192, control_bytes_per_s=4096),
                scope='确定性正确性业务，许可脚本独立标记，无性能排名'))
    return dict(stage='n5a_correctness', status='prepared', networks=networks, execution_order=entries,
        expected_cases=8, business_seeds=[], independent_test=False,
        observation_period_us=100000, decision_period_us=1000000, capacity=1,
        duration_us=10000000, measurement_window_us=[2000000, 10000000],
        work_motif=dict(period_us=125000, first_packet_us=10000, per_flow_phase_us=5000,
            manual_probes=[dict(offset_us=t, probe=p) for t, p in [(3200000, 'old_session'), (5200000, 'old_report'), (6200000, 'outside_path')]]),
        source_sha256={n: sha(ROOT / n) for n in source_files()},
        parameter_reference=dict(path='data/observer-comparison-plan/n4c-methods-01/method-contract.json',
            sha256=sha(ROOT / 'data/observer-comparison-plan/n4c-methods-01/method-contract.json')),
        cost_layers='每条采样出口带内、导出尝试、API序列化、本地通知有效负载分别保留',
        topology_scope='固定双入口合流链，3至8采样节点，首轮3/2与5/4，无任意图声明')


def validate(plan, current=False):
    if plan['status'] != 'prepared' or plan['expected_cases'] != 8 or plan['business_seeds'] or plan['independent_test']:
        raise ValueError('正确性试点范围改变')
    if set(plan['networks']) != {'legacy-3n-2f','expanded-5n-4f'}:
        raise ValueError('拓扑集合不符')
    for name, n, f in [('legacy-3n-2f',3,2),('expanded-5n-4f',5,4)]:
        spec=FixedNetwork(plan['networks'][name])
        if spec.record['sampling_nodes']!=n or len(spec.record['flows'])!=f:
            raise ValueError('试点节点和流数改变')
    if (plan['observation_period_us'],plan['decision_period_us'],plan['capacity'],plan['duration_us'],plan['measurement_window_us'])!=(100000,1000000,1,10000000,[2000000,10000000]):
        raise ValueError('共同观察、容量或测量窗口改变')
    expected = [(n, m) for n in ('legacy-3n-2f', 'expanded-5n-4f') for m in ('static', 'phase_joint', 'phase_guard', 'manual')]
    if [(e['network_id'], e['method']) for e in plan['execution_order']] != expected:
        raise ValueError('完整拓扑和方法案例不符')
    for i, e in enumerate(plan['execution_order']):
        spec=FixedNetwork(plan['networks'][e['network_id']])
        if e['name']!=e['network_id']+'-'+e['method'] or e['position'] != i or len(e['initial_config']) != len(spec.keys):
            raise ValueError('配置长度或执行顺序错误')
        if e['budget']!=dict(link_bytes_per_s={v:4096 for v in spec.links.values()},export_bytes_per_s=8192,control_bytes_per_s=4096):
            raise ValueError('预声明正确性限额改变')
    if current:
        for name, digest in plan['source_sha256'].items():
            if sha(ROOT / name) != digest: raise ValueError('冻结后源码改变 ' + name)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', type=Path, required=True)
    out = p.parse_args().output
    plan = definition(); validate(plan, True)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'stage-plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    (out / 'manifest.json').write_text(json.dumps(dict(status='prepared', artifact_sha256={'stage-plan.json': sha(out / 'stage-plan.json')}), indent=2) + '\n')
    print(json.dumps(dict(status='prepared', cases=8, business_seeds=[], inputs_generated=False, network_started=False)))
