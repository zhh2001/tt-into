"""用已消费网络记录验收共同网格和自有费用接口，不生成新测试业务。"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from collector.tagged_codec import WireRecord
from orchestration.adaptive import AdaptiveSettings, select
from orchestration.control_ledger import API, NOTICE, ControlLedger, notice_payload
from orchestration.event_observation import CacheState, Confirmation, CountObservation, DemandNotification, OnlineView, ReceivedReport
from orchestration.event_schema import Demand, FlowId, ObjectId, RpcCost
from orchestration.matched_grid import ControlCaps, GRID_US, METHODS, MatchedProxy, settings, validate_config
from orchestration.rolling import Budget, choose, configurations
from rolling_workloads import MANAGEMENT
from run_rolling_reference import plain, write_gzip, write_json

SOURCES = ('src/orchestration/control_ledger.py', 'src/orchestration/matched_grid.py',
           'src/orchestration/adaptive.py', 'src/orchestration/rolling.py',
           'src/orchestration/event_engine.py', 'src/orchestration/event_schema.py',
           'src/orchestration/event_metrics.py', 'src/orchestration/event_observation.py',
           'src/collector/tagged_codec.py', 'src/orchestration/metrics.py',
           'experiments/run_matched_preflight.py', 'experiments/check_matched_preflight.py',
           'experiments/run_rolling_reference.py', 'experiments/rolling_workloads.py',
           'experiments/rolling_rpc_cost.py', 'src/control/bmv2.py', 'tests/test_matched_grid.py', 'tests/test_adaptive.py')
ONLINE_KINDS = ('installation', 'cold_start_reset', 'online_observation', 'update', 'reconciliation', 'controlled_failed_read')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []


def decode_view(d):
    def flow(r):
        return FlowId(**r)
    def obj(r):
        return ObjectId(**r)
    return OnlineView(d['now_us'],
        tuple(ReceivedReport(r['received_us'], flow(r['flow']), tuple(WireRecord(**v) for v in r['records'])) for r in d['reports']),
        tuple(CountObservation(**dict(r, flow=flow(r['flow']))) for r in d['counts']),
        tuple(Confirmation(**dict(r, flow=flow(r['flow']))) for r in d['confirmations']),
        tuple(CacheState(**dict(r, object_id=obj(r['object_id']))) for r in d['cache']),
        tuple(DemandNotification(r['received_us'], r['version'], tuple(Demand(**dict(v, object_id=obj(v['object_id'])))
                                                                   for v in r['demands'])) for r in d['notifications']))


def own_ledger(ops, notices, origin):
    ledger = ControlLedger()
    for i, r in enumerate(ops):
        # 诊断寄存器清零也属于 cold_start_reset，须按实际寄存器排除。
        register = r['arguments'][0] if r['arguments'] else ''
        diagnostic = r['method'] == 'bm_register_read_all' or register.startswith(('TTIngress.trace_', 'TTEgress.trace_'))
        if r['kind'] in ONLINE_KINDS and not diagnostic:
            ledger.completed_rpc(f'rpc-{i}', r, origin)
    for case, entries in notices.items():
        for i, r in enumerate(entries):
            payload = bytes.fromhex(r['payload_hex'])
            if len(payload) != r['payload_bytes'] or payload != notice_payload(r['message']):
                raise ValueError('实际通知与发送接口编码不符')
            ledger.received_notice(f'{case}-notice-{i}', payload, r['received_ns'], origin)
    return ledger


def measured_fees(base):
    rows_all = [r for variant in ('ordinary', 'diagnostics') for r in rows(base / variant / 'rpc-operations.jsonl')]
    specs = {'threshold_us': ('TTEgress.time_threshold', True), 'enabled': ('TTEgress.max_count', True),
             'read': ('TTIngress.packet_count', True), 'failed_write': ('TTEgress.missing_for_fault', False),
             'failed_read': ('TTIngress.missing_for_fault', False), 'reconciliation': ('TTEgress.max_count', True)}
    calls = {}
    for key, (register, success) in specs.items():
        method = 'bm_register_write' if key in ('threshold_us', 'enabled', 'failed_write') else 'bm_register_read'
        actual = [r for r in rows_all if r['method'] == method and r['arguments'][0] == register and r['success'] == success
                  and (key != 'reconciliation' or r['kind'] == 'reconciliation')]
        lengths = {(r['request_serialized_bytes'], r['reply_serialized_bytes']) for r in actual}
        if len(lengths) != 1:
            raise ValueError('费用参照不完整或有多个长度')
        request, reply = next(iter(lengths))
        calls[key] = dict(method=method, register=register, success=success,
                          request_bytes=request, reply_bytes=reply, matched_calls=len(actual),
                          provenance='N3 两版本实际序列化计数，错误长度仅对应指定受控错误')
    return {'calls': calls, 'scope': '实际 API 字节，非管理帧；失败长度不推广到任意异常'}


def matched_management(fees):
    def cost(key):
        r = fees['calls'][key]
        return RpcCost(r['request_bytes'], r['reply_bytes'], r['provenance'])
    # notice_cost 仅为旧类初始化兼容，候选控制费用已由 MatchedProxy 独立替换。
    return replace(MANAGEMENT, rpc_cost=cost('threshold_us'),
                   write_costs={k: cost(k) for k in ('enabled', 'threshold_us')}, read_cost=cost('read'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--n3', type=Path, default=ROOT / 'data/into-validation/n3-validation-01')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    manifest = {'status': 'running', 'started_utc': datetime.now(timezone.utc).isoformat(), 'kind': 'matched_grid_preflight',
                'scope': '已消费 N3 合法视图与费用核对，未开展新训练/校准/保留性能矩阵',
                'n3_path': str(args.n3.resolve().relative_to(ROOT)), 'n3_manifest_sha256': sha(args.n3 / 'manifest.json'),
                'settings': settings(), 'methods': METHODS, 'configuration_count': 6561,
                'control_caps': ControlCaps(1200, 128), 'measurement_window_us': [2000000, 10000000],
                'reserved_calibration_seeds': [1301, 1423], 'reserved_heldout_seeds': [1601, 1723, 1871, 1999],
                'reserved_workloads_generated': False, 'source_sha256': {n: sha(ROOT / n) for n in SOURCES}}
    for name in SOURCES:
        p = out / 'executed-source' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, p)
    write_json(out / 'manifest.json', manifest)
    try:
        fees = measured_fees(args.n3)
        write_json(out / 'rpc-reference.json', fees)
        management = matched_management(fees)
        summaries, cases = [], []
        for variant in ('ordinary', 'diagnostics'):
            base = args.n3 / variant
            ops = rows(base / 'rpc-operations.jsonl')
            source = load(base / 'manifest.json')
            notices = {c['name']: rows(base / c['name'] / 'notices.jsonl') for c in source['cases']}
            for case in source['cases']:
                origin = case['origin_monotonic_ns']
                ledger = own_ledger(ops, notices, origin)
                prefix = []
                for d in rows(base / case['name'] / 'decisions.jsonl'):
                    cutoff = d['cutoff_ns'] - origin
                    snapshot = ledger.snapshot(cutoff)
                    window = snapshot.window(2000000000, min(10000000000, max(2000000000, cutoff + 1)))
                    prefix.append({'cutoff_ns': cutoff, 'known_charge_count': len(snapshot.charges), 'window': window})
                start, end = [t * 1000 for t in case['measurement_window_us']]
                final = ledger.snapshot(case['ended_monotonic_ns'] - origin)
                entry = {'variant': variant, 'case': case['name'], 'origin_ns': origin,
                         'measurement_window_ns': [start, end], 'prefixes': prefix,
                         'final_cutoff_ns': final.cutoff_ns,
                         'charges': ledger.snapshot(10**30).charges, 'final_usage': final.window(start, end),
                         'source_paths': {'rpc': str((base / 'rpc-operations.jsonl').relative_to(ROOT)),
                                          'case': str((base / case['name']).relative_to(ROOT))}}
                cases.append(entry)
                summaries.append({'variant': variant, 'case': case['name'], 'decision_prefixes': len(prefix),
                                  'final_usage': entry['final_usage']})
        write_gzip(out / 'ledger-cases.json.gz', cases)
        write_json(out / 'ledger-summary.json', summaries)
        base = args.n3 / 'ordinary'
        case = load(base / 'causal_loop/result.json')
        ops = rows(base / 'rpc-operations.jsonl')
        ledger = own_ledger(ops, {'causal_loop': rows(base / 'causal_loop/notices.jsonl')}, case['origin_monotonic_ns'])
        decisions = rows(base / 'causal_loop/decisions.jsonl')
        diagnostic = next(d for d in decisions if d['scheduled_offset_us'] == 7000000)
        view = decode_view(diagnostic['view'])
        snapshot = ledger.snapshot(diagnostic['cutoff_ns'] - case['origin_monotonic_ns'])
        budget = Budget({'e1': 3000, 'e2': 3000, 'e3': 4000}, 6000, 1200)
        parameters, caps = AdaptiveSettings(gain_margin=.05), ControlCaps(1200, 128)
        def make(temporal=True, guarded=True):
            return MatchedProxy(view, management, budget, settings(), parameters, snapshot, caps, temporal=temporal, guarded=guarded)
        exact = make()
        started = time.perf_counter_ns()
        candidates = [exact.evaluate(c) for c in configurations(management, settings())]
        exact_elapsed = time.perf_counter_ns() - started
        eligible = [r for r in candidates if r['update_allowed']]
        best_joint = min(eligible, key=exact.rank)
        best_local = min(eligible, key=lambda r: exact.rank(r, True))
        limited_joint, limited_local = choose(make(), 'joint', exact.current), choose(make(), 'local', exact.current)
        write_gzip(out / 'exact-candidates.json.gz', candidates)
        write_json(out / 'solver-diagnostic.json', {'input_view': view, 'control_snapshot': snapshot, 'budget': budget,
            'parameters': parameters, 'configuration_count': len(candidates), 'eligible_count': len(eligible),
            'exact_joint': best_joint, 'exact_local': best_local, 'limited_joint': limited_joint,
            'limited_local': limited_local, 'exact_elapsed_ns': exact_elapsed,
            'joint_rank_matches': exact.rank(best_joint) == exact.rank(limited_joint['forecast']),
            'local_rank_matches': exact.rank(best_local, True) == exact.rank(limited_local['forecast'], True),
            'scope': '同一合法预测代理，非实际未来最优或方法性能比较'})
        smoke = []
        for d in decisions:
            v = decode_view(d['view'])
            s = ledger.snapshot(d['cutoff_ns'] - case['origin_monotonic_ns'])
            for method in METHODS:
                temporal, guarded = method in ('phase_joint', 'phase_guard', 'phase_local_guard'), method in ('uniform_guard', 'phase_guard', 'phase_local_guard')
                p = MatchedProxy(v, management, budget, settings(), parameters, s, caps, temporal=temporal, guarded=guarded)
                selected = select(p, p.current, method == 'phase_local_guard') if method not in METHODS[:4] else choose(p, method, p.current)
                validate_config(selected['config'], management)
                if selected['evaluated_candidates'] > settings().max_candidates:
                    raise ValueError('有限搜索超限')
                smoke.append({'time_us': v.now_us, 'method': method, 'selected': selected,
                              'ledger_spend': p.budget_diagnostic['control_ledger_window']})
        write_gzip(out / 'interface-smoke.json.gz', smoke)
        regression = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=ROOT,
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (out / 'unit-tests.txt').write_text(regression.stdout)
        if regression.returncode:
            raise ValueError('最终单元回归失败')
        manifest.update(status='passed', cases=len(cases), decision_prefixes=sum(len(c['prefixes']) for c in cases),
                        interface_smoke_decisions=len(smoke), exact_candidates=len(candidates), unit_exit_code=0)
    except Exception as exc:
        manifest.update(status='failed', error=str(exc))
        (out / 'failure.txt').write_text(traceback.format_exc())
        raise
    finally:
        manifest['finished_utc'] = datetime.now(timezone.utc).isoformat()
        manifest['artifact_sha256'] = {str(p.relative_to(out)): sha(p) for p in out.rglob('*')
                                       if p.is_file() and p.name != 'manifest.json' and 'executed-source' not in p.parts}
        write_json(out / 'manifest.json', manifest)
    print(json.dumps({k: manifest[k] for k in ('status', 'cases', 'decision_prefixes', 'interface_smoke_decisions', 'exact_candidates')}))


if __name__ == '__main__':
    main()
