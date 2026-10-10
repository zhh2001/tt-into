"""沿用独立执行合同，并核对预算估计的观察出处与校准隔离。"""
import argparse
from collections import Counter
import itertools
import gzip
import hashlib
import json
import math
from pathlib import Path

from check_rolling_reference import audit as base_audit, check_raw, flow, load, near, same, violation


def report_prefix(view):
    seen, latest, reports = set(), {}, []
    for r in view['reports']:
        identity = flow(r['flow']), tuple((x['node'], x['hop'], x['generated_local_us'], x['queue_packets']) for x in r['records'])
        if identity in seen:
            continue
        seen.add(identity)
        fresh = False
        for record in r['records']:
            key = flow(r['flow']), record['node']
            stamp = record['generated_local_us']
            if key not in latest or 0 < (stamp - latest[key]) % (1 << 48) < 2**47:
                latest[key] = stamp
                fresh = True
        if fresh:
            reports.append(r)
    return reports


def header_forecast(decision, routes, settings, temporal):
    counts = Counter()
    now, end = decision['time_us'], decision['time_us'] + settings['horizon_us']
    if temporal:
        for p in decision['profiles']:
            if not p['burst_packets']:
                continue
            period, anchor = p['period_us'], p['anchor_us']
            block = anchor + math.floor((now - anchor) / period) * period
            stamps = []
            limit = math.ceil(settings['maximum_rate_pps'] * settings['horizon_us'] / 1e6)
            while block < end and len(stamps) < limit:
                for index in range(p['burst_packets']):
                    stamp = math.ceil(block + index * p.get('inner_spacing_us', 0))
                    if now < stamp < end and len(stamps) < limit:
                        stamps.append(stamp)
                block += period
            counts[flow(p['flow'])] += len(stamps)
    else:
        for r in decision['rates']:
            rate = r['rate_pps']
            if rate is None or rate <= 0:
                continue
            rate = min(rate, settings['maximum_rate_pps'])
            period = 1e6 / rate
            stamp = now + ((r['flow']['tag'] % 31) + .5) / 31 * period
            while stamp < end:
                counts[flow(r['flow'])] += 1
                stamp += period
    links = dict.fromkeys(('e1', 'e2', 'e3'), 0)
    for f, number in counts.items():
        for o in routes[f]:
            links[f'e{o["node"]}'] += 12 * number
    return links


def check_adaptive_raw(raw, manifest, parameters):
    if raw['summary']['method'] in ('static', 'feedback', 'local', 'joint'):
        return 0
    routes = {flow(r['flow']): r['objects'] for r in manifest['topology']['routes']}
    settings, method = manifest['settings'], raw['summary']['method']
    temporal = method != 'uniform_guard'
    expected_history, checks = [], 0
    for d in raw['decisions']:
        now, view = d['time_us'], d['view']
        same(d['budget_history'], expected_history, '只能使用过去自身在线预测')
        reports = report_prefix(view)
        rates = {flow(r['flow']): r['rate_pps'] for r in d['rates']}
        for p in d['profiles']:
            f = flow(p['flow'])
            delay = (len(routes[f]) - 1) * settings['nominal_hop_us'] + 2 * settings['nominal_egress_us'] + settings['nominal_report_delay_us']
            stamps = [r['received_us'] - delay for r in reports
                      if flow(r['flow']) == f and now - parameters['lookback_us'] <= r['received_us'] <= now]
            same(p['observed_report_count'], len(stamps), '机会支持来自报告前缀')
            same(p['rate_pps'], rates[f], '机会速率出处')
            if p['mode'] in ('periodic_cluster', 'count_period_with_report_phase'):
                if p['anchor_us'] not in stamps:
                    raise ValueError('相位锚点不是合法报告先验时间')
                if p['mode'] == 'periodic_cluster' and p['visible_cluster_count'] < 3:
                    raise ValueError('群证据不足')
            if p['mode'] == 'count_period_with_report_phase':
                near(p['period_us'], 1e6 / min(rates[f], settings['maximum_rate_pps']), '周期须来自付费速率')

        diag = d['budget_diagnostic']
        start = parameters['measurement_start_us']
        past = [h for h in expected_history if start <= h['time_us'] < now]
        estimate = diag['spend']
        headers = dict.fromkeys(('e1', 'e2', 'e3'), 0.0)
        for row in estimate['source_count_inputs']:
            f = flow(row['flow'])
            available = [c for c in view['counts'] if flow(c['flow']) == f]
            anchor = next((r for r in available if r['issued_us'] >= start), None)
            last = available[-1] if available else None
            same(row['anchor_read_id'], None if anchor is None else anchor['read_id'], '基础头计数锚点')
            same(row['last_read_id'], None if last is None else last['read_id'], '基础头最后响应')
            number = 0.0
            if anchor and last:
                delta = (last['count'] - anchor['count']) % (1 << 32)
                if delta < 2**31:
                    number = delta + max(0, rates.get(f) or 0) * max(0, now - last['available_us']) / 1e6
            near(row['estimated_window_packets'], number, '基础头尾段外推')
            for o in routes[f]:
                headers[f'e{o["node"]}'] += 12 * number
        meta = dict.fromkeys(headers, 0)
        exported = 0
        for r in reports:
            if r['received_us'] < start:
                continue
            exported += max(60, 46 + 12 * len(r['records']))
            for record in r['records']:
                for o in routes[flow(r['flow'])][record['hop'] - 1:]:
                    meta[f'e{o["node"]}'] += 12
        same(estimate['received_metadata_lower_bytes'], meta, '已收集合记录费用出处')
        same(estimate['received_export_lower_bytes'], exported, '已收集合导出费用出处')
        for link in headers:
            near(estimate['header_bytes'][link], headers[link], '基础头估计字节')
            predicted = sum(max(0, h['cost']['inband_per_link_bytes'].get(link, 0) - h['header_bytes'].get(link, 0)) for h in past)
            near(estimate['predicted_metadata_bytes'][link], predicted, '历史记录费用估计')
            near(estimate['inband_bytes'][link], headers[link] + max(meta[link], predicted), '剩余预算带内输入')
        predicted_export = sum(h['cost']['export_attempt_bytes'] for h in past)
        near(estimate['predicted_export_bytes'], predicted_export, '历史导出估计')
        near(estimate['export_bytes'], max(exported, predicted_export), '导出损伤不能直接免责')
        control = sum(sum(h['cost']['control_serialized_bytes_by_kind'].values()) for h in past)
        near(estimate['control_bytes'], control, '自身已计划控制费用')
        duration = (parameters['measurement_end_us'] - start) / 1e6
        remaining = (parameters['measurement_end_us'] - max(now, start)) / 1e6
        near(diag['remaining_s'], remaining, '剩余窗口')
        nominal, effective = diag['nominal_budget'], diag['effective_budget']
        for link, cap in nominal['link_bytes_per_s'].items():
            expected = max(0, (cap * duration - estimate['inband_bytes'][link]) / remaining) * (1 - parameters['reserve_fraction'])
            near(effective['link_bytes_per_s'][link], expected, '剩余带内费用率')
        for field, spend in [('export_bytes_per_s', estimate['export_bytes']), ('control_bytes_per_s', estimate['control_bytes'])]:
            expected = max(0, (nominal[field] * duration - spend) / remaining) * (1 - parameters['reserve_fraction'])
            near(effective[field], expected, '剩余导出或控制费用率')
        if method != 'phase_joint':
            notice = max(view['notifications'], key=lambda n: (n['version'], n['received_us']))
            ready = all(sum(flow(c['flow']) == f and c['issued_us'] > notice['received_us'] for c in view['counts']) >= 2 for f in routes)
            if not ready:
                same(d['selected']['config'], d['current_config'], '通知后观察不足须保持配置')
        forecast = d['selected']['forecast']
        if forecast is not None:
            expected_history.append({'time_us': now, 'cost': forecast['cost'],
                                     'header_bytes': header_forecast(d, routes, settings, temporal),
                                     'basis': '过去自身选择的在线预测，不是执行器账本'})
        checks += 1
    return checks


def audit(run):
    result = base_audit(run)
    manifest = load(run / 'manifest.json')
    seeds = [set(manifest[k]) for k in ('train_seeds', 'calibration_seeds', 'test_seeds', 'previous_consumed_test_seeds')]
    if any(a & b for i, a in enumerate(seeds) for b in seeds[i + 1:]):
        raise ValueError('校准或保留种子被复用')
    frozen = load(run / 'frozen-parameters.json')
    parameters = frozen['parameters']
    all_rows = [json.loads(line) for line in (run / 'calibration-summary.jsonl').read_text().splitlines()]
    methods = ('uniform_guard', 'phase_guard', 'phase_local_guard')
    expected = set(itertools.product(manifest['scenarios'], manifest['budgets'], manifest['calibration_seeds'], methods, range(len(parameters))))
    same(Counter((r['scenario'], r['budget_level'], r['seed'], r['method'], r['parameter_index']) for r in all_rows),
         Counter(expected), '相同调参限额及全部校准运行')
    scores = []
    for method in methods:
        for index, p in enumerate(parameters):
            rows = [r for r in all_rows if r['method'] == method and r['parameter_index'] == index]
            score = [sum(not r['feasible'] for r in rows), sum(r['excess'] for r in rows),
                     sum(r['loss'] for r in rows) / len(rows), sum(r['update_rpc_count'] for r in rows),
                     p['gain_margin'], p['reserve_fraction']]
            scores.append({'method': method, 'parameter_index': index, 'parameters': p, 'score': score, 'runs': len(rows)})
        best = min((tuple(s['score']), s['parameter_index']) for s in scores if s['method'] == method)[1]
        same(frozen['selected_indices'][method], best, '冻结参数只取校准结果')
    same(load(run / 'calibration-scores.json'), scores, '完整校准得分')
    selections = load(run / 'training-selections.json')
    fees = load(run / 'rpc-calibration.json')['calls']
    extra, cal_totals = 0, Counter()
    for row in all_rows:
        s, b, seed, method, index = row['scenario'], row['budget_level'], row['seed'], row['method'], row['parameter_index']
        raw = load(run / 'calibration-runs' / f'{s}-{b}-{seed}-{method}-p{index}.json.gz')
        workload = load(run / 'calibration-workloads' / f'{s}-{seed}.json.gz')
        same(raw['summary'], row, '校准原始汇总')
        trained = next(r['config'] for r in selections if r['scenario'] == s and r['budget_level'] == b)
        cal_totals.update(check_raw(workload, raw, manifest['budget_definition'][s][b], trained, manifest, fees))
        extra += check_adaptive_raw(raw, manifest, parameters[index])
    for row in load(run / 'summary.json'):
        s, b, seed, method = row['scenario'], row['budget_level'], row['seed'], row['method']
        raw = load(run / 'runs' / f'{s}-{b}-{seed}-{method}.json.gz')
        p = parameters[frozen['selected_indices'][method]] if method in methods else parameters[0]
        extra += check_adaptive_raw(raw, manifest, p)
    result.update(calibration_runs=len(all_rows), calibration_totals=dict(cal_totals), adaptive_decision_checks=extra)
    return result


def audit_density(run):
    manifest = load(run / 'manifest.json')
    same(manifest['status'], 'passed', '密网格运行状态')
    for name, digest in manifest['artifact_sha256'].items():
        same(hashlib.sha256((run / name).read_bytes()).hexdigest(), digest, '密网格产物摘要')
    for name, digest in manifest['source_sha256'].items():
        same(hashlib.sha256((run / 'executed-source' / name).read_bytes()).hexdigest(), digest, '密网格执行源码')
    groups = {}
    with gzip.open(run / 'training.jsonl.gz', 'rt') as stream:
        for line in stream:
            r = json.loads(line)
            key = r['scenario'], tuple(r['config'])
            groups.setdefault(key, []).append(r)
            near(r['excess'], violation(r['cost'], manifest['budget_definition'][r['scenario']]['high']), '密网格训练超限')
    configs = set(itertools.product([None] + manifest['settings']['grid_us'], repeat=4))
    selections = load(run / 'training-selections.json')
    for s in manifest['scenarios']:
        rows = [r for (scenario, _), entries in groups.items() if scenario == s for r in entries]
        same(Counter((r['seed'], tuple(r['config'])) for r in rows),
             Counter((seed, config) for seed in manifest['train_seeds'] for config in configs), '密网格完整调参')
        scores = []
        for config in configs:
            relevant = groups[s, config]
            score = (max(r['excess'] for r in relevant), sum(r['loss'] for r in relevant) / len(relevant),
                     sum(v is not None for v in config), tuple(-1 if v is None else v for v in config))
            scores.append((score, config))
        same(tuple(next(r['config'] for r in selections if r['scenario'] == s)), min(scores)[1], '密网格训练选择')
    totals = Counter()
    summary = load(run / 'summary.json')
    same(Counter((r['scenario'], r['seed']) for r in summary),
         Counter(itertools.product(manifest['scenarios'], manifest['test_seeds'])), '密网格验证全集')
    fees = load(run / 'rpc-calibration.json')['calls']
    for row in summary:
        scenario, seed = row['scenario'], row['seed']
        raw = load(run / 'runs' / f'{scenario}-high-{seed}-static.json.gz')
        same(raw['summary'], row, '密网格原始汇总')
        workload = load(run / 'workloads' / f'{scenario}-{seed}.json.gz')
        trained = next(r['config'] for r in selections if r['scenario'] == scenario)
        totals.update(check_raw(workload, raw, manifest['budget_definition'][scenario]['high'], trained, manifest, fees))
    return {'status': 'passed', 'training_runs': sum(map(len, groups.values())), 'validation_runs': len(summary),
            **dict(totals), 'scope': manifest['scope']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.run), ensure_ascii=False, indent=2))
