"""汇总已核对规模数据的软件观察、求解和部署变化，不修改测量。"""
import hashlib
import json
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / 'data/network-scale-comparison-validation/n5d-comparison-02'
OUTPUT = Path(__file__).parent / 'software-diagnostics.json'


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


summary = read(DATA / 'summary.json')
verification = {(r['batch_id'], r['case']): r for r in read(DATA / 'verification.json')['cases']}
case_diagnostics = []
for row in summary:
    case = DATA / row['batch_id'] / row['case']
    decisions = rows(case / 'decisions.jsonl')
    observations = rows(case / 'report-observations.jsonl')
    samples = read(case / 'controller-samples.json')
    rpc = rows(DATA / row['batch_id'] / 'rpc-operations.jsonl')
    result = read(case / 'result.json')
    actual_rpc = rpc[result['rpc_start_index']:result['rpc_stop_index']]
    online_rpc = [r for r in actual_rpc if r['kind'] in ('online_observation', 'update')]
    solver = [(r['solver_finished_ns'] - r['solver_started_ns']) / 1e6 for r in decisions
              if r.get('solver_started_ns') is not None and r.get('solver_finished_ns') is not None]
    reads = [(r['availability']['read_finished_ns'] - r['availability']['read_started_ns']) / 1e6
             for r in observations if not r['skipped']]
    v = verification[row['batch_id'], row['case']]
    case_diagnostics.append(dict(batch_id=row['batch_id'], case=row['case'], network_id=row['network_id'],
        scenario=row['scenario'], method=row['method'], skipped_observations=v['skipped_observations'],
        visible_reports=v['visible_reports'], shared_cache_advances=v['shared_cache_advances'],
        first_notice_available=bool(decisions[0]['view']['notifications']),
        solver_mean_ms=mean(solver) if solver else 0, solver_max_ms=max(solver, default=0),
        read_max_ms=max(reads, default=0), observer_max_delay_ms=row['observer_max_delay_ms'],
        online_rpc_max_ms=max(((r['after_monotonic_ns'] - r['before_monotonic_ns']) / 1e6 for r in online_rpc), default=0),
        source_max_lateness_ms=max(r['schedule_lateness_ns'] / 1e6 for r in rows(case / 'send.jsonl')),
        wall_clock_compatible=read(case / 'window-costs.json')['wall_clock']['compatible'],
        tasks=len(read(case / 'tasks.json')), controller_samples=len(samples),
        initial_notice_payload_bytes=sum(r['payload_bytes'] for r in rows(case / 'notices.jsonl')),
        host=read(case / 'host.json')['usage']))

groups = []
for network, scene, method in sorted({(r['network_id'], r['scenario'], r['method']) for r in summary}):
    members = [r for r in case_diagnostics if (r['network_id'], r['scenario'], r['method']) == (network, scene, method)]
    groups.append(dict(network_id=network, scenario=scene, method=method, cases=len(members),
        skipped_observations=sum(r['skipped_observations'] for r in members),
        solver_mean_ms=mean(r['solver_mean_ms'] for r in members), solver_max_ms=max(r['solver_max_ms'] for r in members),
        observer_max_delay_ms=max(r['observer_max_delay_ms'] for r in members),
        first_notice_available=sum(r['first_notice_available'] for r in members)))

repeats = read(DATA / 'deployment-repeats.json')
record = dict(status='passed', source_manifest_sha256=digest(DATA / 'manifest.json'), script_sha256=digest(Path(__file__)),
    cases=case_diagnostics, groups=groups,
    totals=dict(cases=len(summary), tasks=sum(r['tasks'] for r in case_diagnostics),
        visible_reports=sum(r['visible_reports'] for r in case_diagnostics),
        shared_cache_advances=sum(r['shared_cache_advances'] for r in case_diagnostics),
        controller_samples=sum(r['controller_samples'] for r in case_diagnostics),
        first_notice_available=sum(r['first_notice_available'] for r in case_diagnostics),
        wall_clock_compatible=sum(r['wall_clock_compatible'] is True for r in case_diagnostics),
        read_max_ms=max(r['read_max_ms'] for r in case_diagnostics),
        solver_max_ms=max(r['solver_max_ms'] for r in case_diagnostics),
        online_rpc_max_ms=max(r['online_rpc_max_ms'] for r in case_diagnostics),
        source_max_lateness_ms=max(r['source_max_lateness_ms'] for r in case_diagnostics),
        observer_max_delay_ms=max(r['observer_max_delay_ms'] for r in case_diagnostics),
        deployment_repeats=len(repeats), same_sample_positions=sum(r['same_sample_positions'] for r in repeats),
        same_decision_sequence=sum(r['same_decision_sequence'] for r in repeats),
        controller_overlap=sum(r['controller_common_overlap'] for r in repeats),
        collector_overlap=sum(r['collector_common_overlap'] for r in repeats)),
    scope='已核对实际日志的描述汇总，宿主端点不是峰值，耗时不是硬期限或硬件结果')
OUTPUT.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(record['totals']))
