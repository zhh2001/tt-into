"""保存通用事件模型的确定性合同验证，不进行网络或性能排名。"""
import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from orchestration.event_engine import EventEngine
from orchestration.event_metrics import evaluate_tasks, window_costs
from orchestration.event_observation import estimate_rates
from orchestration.event_schema import FlowId, Hop, Node, ObjectId, Packet, Read, RpcCost, Task, Write

SOURCES = ('src/orchestration/event_schema.py', 'src/orchestration/event_engine.py',
           'src/orchestration/event_metrics.py', 'src/orchestration/event_observation.py',
           'src/orchestration/metrics.py', 'src/orchestration/path_reference.py',
           'src/orchestration/budget_model.py', 'src/orchestration/model.py',
           'src/collector/tagged_codec.py', 'tests/test_event_engine.py',
           'experiments/run_event_validation.py', 'experiments/check_event_validation.py')
COST = RpcCost(10, 4, '预声明合成 RPC 请求 10 B、回复 4 B，仅用于账本核对')


def plain(value):
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def write_json(path, value):
    path.write_text(json.dumps(plain(value), ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def install(flow, route, threshold, disabled=()):
    return tuple(Write(f'install-{flow.label}-{o.node}-{field}', o.node, flow, field, value, 1,
                       -20, -19, -18, COST, 'installation')
                 for o in route for field, value in [('enabled', o.node not in disabled), ('threshold_us', threshold)])


def business(pid, flow, route, times, arrivals=None, mtus=None):
    hops = tuple(Hop(obj, 'e' + str(obj.node), a, b, 1500 if mtus is None else mtus[i], i + 1)
                 for i, (obj, (a, b)) in enumerate(zip(route, times)))
    sink = times[-1][1] + 1
    return Packet(pid, flow, hops, sink, (sink + 1,) if arrivals is None else arrivals)


def cases():
    f, other = FlowId(19, 1001), FlowId(19, 2002)
    one, two, five = (ObjectId(1),), (ObjectId(1), ObjectId(2)), tuple(ObjectId(n) for n in range(1, 6))

    def case(name, routes, packets, writes, expected, capacity=1, nodes=None, reads=(), checkpoint=None):
        objects = sorted(set(o for route in routes.values() for o in route))
        return {'name': name, 'nodes': [Node(n) for n in sorted({o.node for o in objects})] if nodes is None else nodes,
                'routes': routes, 'packets': packets, 'writes': writes, 'reads': reads, 'capacity': capacity,
                'tasks': [Task('task-' + o.label, o, 0, 120, 20) for o in objects],
                'window_us': [0, 120], 'clip': 2, 'checkpoint_us': checkpoint, 'expected': expected}

    yield case('linear-five-capacity', {f: five}, [business(1, f, five, tuple((2*i, 2*i+1) for i in range(5)))],
               install(f, five, 10), {'selected': {'1': [2, 1]}, 'inband_per_link_bytes':
                                       {'e1': 24, 'e2': 36, 'e3': 36, 'e4': 36, 'e5': 36},
                                       'export_attempt_bytes': 70}, capacity=2)
    a, b = (ObjectId(1), ObjectId(3)), (ObjectId(2), ObjectId(3))
    yield case('fork-shared-object', {f: a, other: b},
               [business(1, f, a, ((0, 1), (2, 3))), business(2, other, b, ((5, 6), (7, 8)))],
               install(f, a, 1, (1,)) + install(other, b, 1, (2,)),
               {'selected': {'1': [3], '2': [3]}, 'inband_per_link_bytes': {'e1': 12, 'e2': 12, 'e3': 48},
                'export_attempt_bytes': 120})
    yield case('cross-packet-order', {f: two},
               [business(1, f, two, ((0, 1), (100, 101))), business(2, f, two, ((10, 11), (20, 21)))],
               install(f, two, 90, (1,)),
               {'selected': {'1': [], '2': [2]}, 'inband_per_link_bytes': {'e1': 24, 'e2': 36},
                'export_attempt_bytes': 60})
    off = Write('inflight-off', 1, f, 'enabled', False, 2, 3, 5, 12, COST)
    yield case('inflight-write', {f: one}, [business(1, f, one, ((0, 10),))], install(f, one, 10) + (off,),
               {'selected': {'1': []}, 'inband_per_link_bytes': {'e1': 12}, 'export_attempt_bytes': 0})
    yield case('failed-write', {f: one}, [business(1, f, one, ((0, 10),))],
               install(f, one, 10) + (Write('failed-off', 1, f, 'enabled', False, 2, 3, 5, 12, COST, success=False),),
               {'selected': {'1': [1]}, 'inband_per_link_bytes': {'e1': 24}, 'export_attempt_bytes': 60})
    yield case('late-duplicate-report', {f: one},
               [business(1, f, one, ((0, 1),), (20, 21)), business(2, f, one, ((10, 11),), (13, 14))],
               install(f, one, 10), {'selected': {'1': [1], '2': [1]},
                                     'inband_per_link_bytes': {'e1': 48}, 'export_attempt_bytes': 120,
                                     'received_report_bytes': 240}, checkpoint=22)
    yield case('offset-clock-wrap', {f: one},
               [business(i, f, one, ((t, t + 1),)) for i, t in enumerate((0, 9, 10), 1)],
               install(f, one, 10), {'selected': {'1': [1], '2': [], '3': [1]},
                                     'inband_per_link_bytes': {'e1': 60}, 'export_attempt_bytes': 120},
               nodes=[Node(1, 250, 8)])
    yield case('downstream-mtu-drop', {f: two},
               [business(1, f, two, ((0, 1), (2, 3)), arrivals=(), mtus=(1500, 140))],
               install(f, two, 10), {'selected': {'1': [1]}, 'inband_per_link_bytes': {'e1': 24, 'e2': 0},
                                     'export_attempt_bytes': 0, 'dropped_packets': [1]})
    reads = (Read('count-1', f, 1, 2, 3, 4, COST), Read('count-2', f, 1, 12, 13, 14, COST))
    yield case('paid-observation', {f: one},
               [business(i, f, one, ((t, t + 1),)) for i, t in enumerate((0, 10, 50), 1)],
               install(f, one, 10), {'selected': {'1': [1], '2': [1], '3': [1]},
                                     'inband_per_link_bytes': {'e1': 72}, 'export_attempt_bytes': 180},
               reads=reads, checkpoint=20)
    yield case('disabled-envelope-floor', {f: one},
               [business(i, f, one, ((t, t + 1),)) for i, t in enumerate((0, 50), 1)],
               install(f, one, 10, (1,)), {'selected': {'1': [], '2': []},
                                         'inband_per_link_bytes': {'e1': 24}, 'export_attempt_bytes': 0})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    run = args.output or ROOT / 'data/event-validation' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run.mkdir(parents=True, exist_ok=False)
    manifest = {'experiment_id': run.name, 'status': 'running', 'kind': 'deterministic_event_contract_validation',
                'started_utc': datetime.now(timezone.utc).isoformat(), 'seed': 0,
                'environment': {'python': platform.python_version(), 'cpu_count': os.cpu_count(),
                                'load_average_at_start': list(os.getloadavg())},
                'scope': '确定性事件模型正确性验证；无网络实验、随机性能估计或方法排名',
                'warmup': '无自动缓存种子，窗口前安装单列，未知任务保留',
                'measurement_window': '各案例 specification.json 中的半开区间',
                'source_sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}}
    source_dir = run / 'executed-source'
    for name in SOURCES:
        path = source_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT/name).read_bytes())
    write_json(run / 'manifest.json', manifest)
    try:
        command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']
        regression = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (run / 'unit-tests.txt').write_text(regression.stdout)
        manifest['unit_regression'] = {'command': 'python3 -m unittest discover -s tests -v',
                                       'exit_code': regression.returncode,
                                       'log': 'unit-tests.txt'}
        manifest['unit_source_sha256'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                        for base in ('src', 'tests', 'experiments')
                                        for p in sorted((ROOT/base).rglob('*.py'))}
        if regression.returncode:
            raise RuntimeError('完整单元回归失败，见 unit-tests.txt')
        from check_event_validation import check_case
        summary = []
        for spec in cases():
            name = spec['name']
            folder = run / name
            folder.mkdir()
            engine = EventEngine(spec['nodes'], spec['routes'], spec['packets'], spec['writes'], spec['reads'], spec['capacity'])
            checkpoint = None
            if spec['checkpoint_us'] is not None:
                view = engine.run_until(spec['checkpoint_us'])
                checkpoint = {'view': view, 'rate_estimates': estimate_rates(view)}
            result = engine.run()
            result['task_metrics'] = evaluate_tasks(spec['tasks'], result['receipts'], spec['clip'])
            result['window_costs'] = window_costs(result['cost_events'], *spec['window_us'],
                                                 {link: 100000 for link in result['inband_per_link_bytes']}, 1000000)
            result['checkpoint'] = checkpoint
            # 管理路径单独序列化，避免 Python 对象 repr 成为配置协议。
            serialized = {k: v for k, v in spec.items() if k != 'routes'}
            serialized['routes'] = [{'flow': flow, 'objects': route} for flow, route in spec['routes'].items()]
            write_json(folder / 'specification.json', serialized)
            write_json(folder / 'result.json', result)
            checks = check_case(plain(serialized), plain(result))
            write_json(folder / 'checks.json', checks)
            summary.append({'case': name, 'status': 'passed', 'check_count': len(checks),
                            'packet_count': len(spec['packets'])})
        write_json(run / 'summary.json', summary)
        manifest.update(status='passed', cases=len(summary), packets=sum(row['packet_count'] for row in summary),
                        independent_checks=sum(row['check_count'] for row in summary))
    except Exception as error:
        manifest.update(status='failed', error=str(error))
        (run / 'failure.txt').write_text(traceback.format_exc())
        raise
    finally:
        manifest['finished_utc'] = datetime.now(timezone.utc).isoformat()
        manifest['artifact_sha256'] = {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in sorted(run.rglob('*')) if p.is_file() and p.name != 'manifest.json'
                                       and 'executed-source' not in p.parts}
        write_json(run / 'manifest.json', manifest)
    print(json.dumps({'run': str(run.relative_to(ROOT)) if run.is_relative_to(ROOT) else str(run),
                      'status': manifest['status'], 'cases': manifest['cases'],
                      'independent_checks': manifest['independent_checks']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
