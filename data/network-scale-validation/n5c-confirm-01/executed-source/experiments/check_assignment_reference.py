"""核对源码快照、调参隔离、独立连续年龄积分及实际前缀字节账本。"""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path


def read(directory, name):
    path = directory / name
    if path.exists():
        return json.loads(path.read_text())
    return json.loads(gzip.decompress((directory / (name + '.gz')).read_bytes()))


def fractions(samples, phases):
    """直接按报告到达与期限穿越分段，不调用原年龄计算器。"""
    output = []
    for phase in phases:
        start, end = phase['start_us'], phase['end_us']
        row = []
        for node, deadline in enumerate(phase['deadlines_us'], 1):
            arrivals = sorted((a, g) for g, a in samples[node])
            latest = max((g for a, g in arrivals if a <= start), default=None)
            grouped = defaultdict(list)
            for a, g in arrivals:
                if start < a < end:
                    grouped[a].append(g)
            bad, left = 0., start
            for right in sorted(grouped) + [end]:
                bad += right - left if latest is None else max(0., right - max(left, latest + deadline))
                if right in grouped:
                    latest = max(grouped[right] + ([] if latest is None else [latest]))
                left = right
            row.append(bad / (end - start))
        output.append(row)
    return output


def execute(packets, schedule, capacity):
    gate, selected, samples, wire, exported = {}, {}, {n: [] for n in (1, 2, 3)}, [0, 0, 0], 0
    index = 0
    for p in packets:
        while index + 1 < len(schedule) and schedule[index + 1]['effective_us'] <= p['gate_us'][0]:
            index += 1
        targets = {(r['node'], r['flow']): r['threshold_us'] for r in schedule[index]['thresholds']}
        chosen = []
        for n, t, g in zip(p['path'], p['gate_us'], p['generated_us']):
            key, threshold = (n, p['flow']), targets[n, p['flow']]
            if threshold is None or (key in gate and t - gate[key] < threshold):
                continue
            if len(chosen) >= min(capacity, (1500 - p['ip_bytes'] - 4) // 12):
                continue
            chosen.append(n)
            gate[key] = t
            if p['export_emitted'] and p['arrival_us'] is not None:
                samples[n].append((g, p['arrival_us']))
        # 记录插入栈首，报告中的顺序与沿路径的选择顺序相反。
        selected[str(p['packet_id'])] = list(reversed(chosen))
        for idx, link in enumerate(p['links']):
            wire[link] += 4 + 12 * sum(n in chosen for n in p['path'][:idx + 1])
        if chosen and p['export_emitted']:
            exported += max(60, 34 + 4 + 12 * len(chosen))
    return selected, samples, wire, exported


def aggregation(results):
    n = len(results)
    return (-sum(r['budget_qualified_weight'] for r in results) / n,
            sum(r['weighted_violation_fraction'] for r in results) / n,
            sum(r['data_total_byte_hops'] for r in results) / n,
            sum(r['runtime_api_total_serialized_bytes'] for r in results) / n)


def audit(directory):
    manifest = read(directory, 'manifest.json')
    if manifest['status'] != 'passed':
        raise ValueError('本核对仅处理已完成运行，失败记录仍保留')
    for name, digest in manifest['artifact_sha256'].items():
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
            raise ValueError('数据摘要不一致: ' + name)
    for name, digest in manifest['source_sha256'].items():
        if hashlib.sha256((directory / 'executed-source' / name).read_bytes()).hexdigest() != digest:
            raise ValueError('实际运行源码摘要不一致: ' + name)
    spec = read(directory, 'specification.json')
    if set(spec['training_seeds']) & set(spec['heldout_seeds']):
        raise ValueError('训练与保留种子重叠')
    workloads = read(directory, 'workloads.json')
    inputs = {(w['split'], w['scenario'], w['seed']): w for w in workloads}
    training = read(directory, 'training-results.json')
    test = read(directory, 'heldout-results.json')
    choices = read(directory, 'selected-parameters.json')
    grouped = defaultdict(list)
    for row in training:
        if row['seed'] not in spec['training_seeds']:
            raise ValueError('非训练种子进入调参')
        grouped[row['capacity'], row['family'], row['parameter_index']].append(row)
    for choice in choices:
        capacity, family = choice['capacity'], choice['family']
        keys = []
        for index in range(len(spec['grid'][family])):
            entries = grouped[capacity, family, index]
            actual = {(r['scenario'], r['seed']) for r in entries}
            expected = {(s, seed) for s in spec['scenarios'] for seed in spec['training_seeds']}
            if len(entries) != len(expected) or actual != expected:
                raise ValueError('训练网格缺失或重复')
            keys.append((*aggregation([r['result'] for r in entries]), index))
        if min(keys)[-1] != choice['parameter_index'] or spec['grid'][family][min(keys)[-1]] != choice['parameters']:
            raise ValueError('所选参数不是仅依训练记录确定的结果')
    calibration = read(directory, 'runtime-api-calibration.json')
    tolerance, verified, max_error = 1e-10, 0, 0.
    for row in test:
        if row['seed'] not in spec['heldout_seeds']:
            raise ValueError('测试种子不在预声明范围')
        choice = next(c for c in choices if c['capacity'] == row['capacity'] and c['family'] == row['family'])
        if choice['parameter_index'] != row['parameter_index']:
            raise ValueError('测试后更换参数')
        w, r = inputs['heldout', row['scenario'], row['seed']], row['result']
        selected, samples, wire, exported = execute(w['packets'], r['schedule'], row['capacity'])
        stored_samples = {int(n): [tuple(sample) for sample in values] for n, values in r['samples'].items()}
        if stored_samples != samples:
            raise ValueError('逐份已收报告与原始轨迹不一致')
        if selected != r['selected'] or wire != r['inband_per_link_bytes'] or exported != r['export_bytes']:
            raise ValueError('独立前缀门控或逐链路费用不一致')
        bad = fractions(samples, w['phases'])
        error = max(abs(a - b) for x, y in zip(bad, r['violation_fractions_by_phase']) for a, b in zip(x, y))
        max_error = max(max_error, error)
        if error > tolerance:
            raise ValueError('独立连续年龄积分不一致')
        control, calls = 0, 0
        previous = None
        for d in r['decisions']:
            now = d['issued_us']
            if d['effective_us'] != now + spec['fixed_effect_delay_us']:
                raise ValueError('固定控制延迟不一致')
            visible = {n: max((g for g, a in samples[n] if a <= now), default=None) for n in (1, 2, 3)}
            if row['family'] != 'tt' and visible != {int(n): g for n, g in d['latest_received_generated_us'].items()}:
                raise ValueError('决策包含未送达报告或遗漏已到达状态')
            targets = {(v['node'], v['flow']): v['threshold_us'] for v in d['thresholds']}
            expected = []
            for key in sorted(targets):
                before, after = None if previous is None else previous[key], targets[key]
                if previous is None:
                    registers = [('TTEgress.max_count', row['capacity'])] if after is not None else []
                elif before == after:
                    registers = []
                elif after is None:
                    registers = [('TTEgress.max_count', 0)]
                else:
                    registers = [('TTEgress.time_threshold', after)] + ([('TTEgress.max_count', row['capacity'])] if before is None else [])
                expected.extend({'node': key[0], 'flow': key[1], 'register': reg, 'value': value} for reg, value in registers)
            if expected != d['writes']:
                raise ValueError('寄存器更新目标或初次启用不一致')
            amount = sum(calibration['write_bytes'][v['register']] for v in expected)
            if amount != d['update_serialized_bytes']:
                raise ValueError('单次更新费用不一致')
            control += amount
            calls += len(expected)
            previous = targets
        if control != r['runtime_api_update_serialized_bytes'] or calls != r['runtime_api_update_calls']:
            raise ValueError('总更新费用不一致')
        total = calibration['installation_bytes'] + control
        feasible = (all(v <= b for v, b in zip(wire, w['budget']['inband_per_link_bytes'])) and
                    exported <= w['budget']['export_bytes'] and total <= w['budget']['runtime_api_serialized_bytes'])
        nodes = [n for n in (1, 2, 3) if all(x[n - 1] <= tolerance for x in bad)]
        weight = sum(spec['weights'][n - 1] for n in nodes)
        if feasible != r['budget_satisfied'] or weight != r['fulfilled_weight'] or (weight if feasible else 0) != r['budget_qualified_weight']:
            raise ValueError('整次预算或履约权重不一致')
        verified += 1
    expected_tests = len(spec['capacities']) * len(spec['grid']) * len(spec['scenarios']) * len(spec['heldout_seeds'])
    if verified != expected_tests or len({(r['capacity'], r['family'], r['scenario'], r['seed']) for r in test}) != expected_tests:
        raise ValueError('保留测试缺失或重复')
    behaviors = defaultdict(list)
    fields = ('selected', 'samples', 'inband_per_link_bytes', 'export_bytes',
              'violation_fractions_by_phase', 'runtime_api_total_serialized_bytes')
    for row in test:
        behaviors[row['capacity'], row['scenario'], row['seed']].append(row['result'])
    matched = sum(all(all(r[field] == group[0][field] for field in fields) for r in group[1:])
                  for group in behaviors.values())
    return {'status': 'passed', 'heldout_execution_checks': verified,
            'training_record_checks': len(training), 'selected_parameter_checks': len(choices),
            'max_independent_violation_absolute_error': max_error,
            'paired_behavior_groups': len(behaviors), 'all_method_behavior_matched_groups': matched,
            'behavior_comparison_fields': fields,
            'input_manifest_sha256': hashlib.sha256((directory / 'manifest.json').read_bytes()).hexdigest(),
            'checker_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'scope': '独立前缀执行、费用、连续年龄及调参隔离核对，不是网络测量或新收益证据'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as output:
        output.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
