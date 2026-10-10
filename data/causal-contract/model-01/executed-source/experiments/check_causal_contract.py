"""独立枚举关键时刻覆盖和全部两步策略，不调用被测模块。"""
import argparse
import hashlib
from itertools import combinations, product
import json
from pathlib import Path


def actions(eligible, capacity):
    return [c for n in range(min(len(eligible), capacity) + 1)
            for c in combinations(sorted(eligible), n)]


def coverage(task, deadline, initial, reports, start, end):
    checks = [(start, True), (end, False)]
    checks += [(a, False) for t, g, a in reports if t == task and start < a < end]
    for at, inclusive in checks:
        latest = [g for t, g, a in reports if t == task and (a <= at if inclusive else a < at)]
        if initial is not None:
            latest.append(initial)
        if not latest or at - max(latest) > deadline:
            return False
    return True


def independently_feasible(admitted, plan, delay, deadlines, budget, capacity, future):
    first, second = plan
    if len(first) > capacity or len(second) > capacity or len(first) + len(second) > budget:
        return False
    if not set(first) <= {1, 2} or not set(second) <= {future}:
        return False
    reports = [(t, at, at + delay) for at, action in [(0, first), (100000, second)] for t in action]
    return all(coverage(t, deadlines[t - 1], -100000, reports, 0, 200000) for t in admitted)


def frontier(inputs):
    cap, budget, delay = inputs['capacity'], inputs['record_budget'], inputs['report_delay_us']
    deadlines = inputs['deadlines_us']
    subsets = sorted(actions([1, 2], 2), key=lambda c: (-len(c), c))
    firsts = actions([1, 2], cap)
    seconds = {t: actions([t], cap) for t in (1, 2)}

    def succeeds(admitted, a, b, future):
        return independently_feasible(admitted, (a, b), delay, deadlines, budget, cap, future)

    offline = [max(len(admitted) for admitted in subsets
                   if any(succeeds(admitted, a, b, t) for a, b in product(firsts, seconds[t])))
               for t in (1, 2)]
    if inputs['branch_hint']:
        causal = max(len(admitted) for admitted in subsets
                     if all(any(succeeds(admitted, a, b, t) for a, b in product(firsts, seconds[t]))
                            for t in (1, 2)))
    else:
        causal = max(len(admitted) for admitted in subsets
                     if any(all(any(succeeds(admitted, a, b, t) for b in seconds[t])
                                    for t in (1, 2)) for a in firsts))
    return offline, causal


def audit(run):
    manifest = json.loads((run / 'manifest.json').read_text())
    if manifest['status'] != 'passed':
        raise ValueError('只能核对完成运行；失败记录需保留')
    for name, digest in manifest['artifact_sha256'].items():
        if hashlib.sha256((run / name).read_bytes()).hexdigest() != digest:
            raise ValueError('产物摘要不一致: ' + name)
    for name, digest in manifest['source_sha256'].items():
        if hashlib.sha256((run / 'executed-source' / name).read_bytes()).hexdigest() != digest:
            raise ValueError('执行源码摘要不一致: ' + name)
    spec = json.loads((run / 'specification.json').read_text())
    rows = json.loads((run / 'frontiers.json').read_text())
    expected = {tuple((c, b, d, delay, h)) for c, b, d, delay, h in product(
        spec['capacities'], spec['record_budgets'], spec['deadlines_us'],
        spec['report_delays_us'], spec['branch_hints'])}
    actual = {(r['input']['capacity'], r['input']['record_budget'], r['input']['deadlines_us'][0],
               r['input']['report_delay_us'], r['input']['branch_hint'])
              for r in rows if r['input']['group'] == 'grid'}
    if actual != expected or len(rows) != len(expected) + 4:
        raise ValueError('参数网格缺失或重复')
    for row in rows:
        inputs, result = row['input'], row['result']
        offline, causal = frontier(inputs)
        if offline != [len(r['admitted']) for r in result['offline']]:
            raise ValueError('离线接纳不一致')
        if causal != result['causal_admitted']:
            raise ValueError('因果接纳不一致')
        plans = result['causal']['plans']
        if not inputs['branch_hint'] and plans[0]['first'] != plans[1]['first']:
            raise ValueError('同一过去使用了不同动作')
        for future, plan in zip((1, 2), plans):
            if not independently_feasible(result['causal']['admitted'],
                                           (plan['first'], plan['second']),
                                           inputs['report_delay_us'], inputs['deadlines_us'],
                                           inputs['record_budget'], inputs['capacity'], future):
                raise ValueError('因果策略证据不成立')
    deployment = json.loads((run / 'deployment.json').read_text())
    for row in deployment:
        p, result = row['input'], row['result']
        selected = sorted(t for t in p['packet_times'] if t >= p['notice_us'] + p['install_delay_us'])
        reports = [(1, t, t + p['report_delay_us']) for t in selected]
        fulfilled = coverage(1, p['deadline_us'], p['initial_generated_us'],
                             reports, p['change_us'], p['end_us'])
        if fulfilled != result['age']['fulfilled'] or len(selected) != result['selected_records']:
            raise ValueError('控制提前量覆盖不一致')
        # 本网格中首个报告之后的周期机会已足够，所有违约均在首报前。
        right = min(p['end_us'], reports[0][2] if reports else p['end_us'])
        before = max(0, right - max(p['change_us'], p['initial_generated_us'] + p['deadline_us']))
        if before != result['age']['violation_us']:
            raise ValueError('违约时长不能由首报等待解释')
    return {'status': 'passed', 'source_hashes': len(manifest['source_sha256']),
            'frontier_cases': len(rows), 'deployment_cases': len(deployment),
            'checks': ['sha256', 'complete_grid', 'independent_critical_time_coverage',
                       'all_two_step_policies', 'shared_first_action', 'reported_witnesses',
                       'first_report_violation_duration']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('核对输出已经存在')
    result = audit(args.run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
