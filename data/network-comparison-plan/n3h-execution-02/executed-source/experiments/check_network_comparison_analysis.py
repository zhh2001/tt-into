"""独立重建比较费用、两观察位置年龄、配对和真实业务分母。"""
import argparse
import csv
import json
from pathlib import Path

from check_matched_network import load, rows, sha, charges_for
from check_network_comparison import audit
from network_entry_bounds import output_frames, costs, generation_bounds
from check_rolling_reference import near, same, clipped_loss


def table(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def check_summary(output, cases):
    base = {r['case']: r for r in table(output / 'summary.csv')}
    case_rows = {r['name']: r for r in table(output / 'case-availability.csv')}
    aggregate = table(output / 'method-aggregate.csv')
    available = table(output / 'report-availability.csv')
    batches = table(output / 'availability-batches.csv')
    notices = table(output / 'notice-availability.csv')
    same(set(base), {c['name'] for c in cases}, '案例汇总分母')
    same(set(case_rows), set(base), '可用性案例分母')
    count = 0
    outputs = output_frames(output, cases)
    rpc = rows(output/'rpc-operations.jsonl')
    for c in cases:
        directory = output / c['name']
        cost = costs(directory, c, outputs[c['name']], charges_for(directory, rpc))
        same(load(directory/'window-costs.json'), cost, '费用由真实输出和调用重建')
        same(base[c['name']]['window_feasibility'], cost['window_feasibility'], '窗口可行性来源')
        age = generation_bounds(directory, c)
        same(load(directory/'generation-bounds.json'), age, '生成时间及完整任务重建')
        for side in ('lower','upper'):
            near(float(base[c['name']]['loss_'+side]), age['loss_'+side], '采集端年龄汇总来源')
        opts = load(directory / 'algorithm-settings.json')
        if cost['measurement_upper_bytes'] is None:
            same(base[c['name']]['normalized_excess_upper'], '', '不可评价费用不作零超限')
        else:
            limits = dict(opts['budget']['link_bytes_per_s'], export=opts['budget']['export_bytes_per_s'],
                          api_serialized=opts['budget']['control_bytes_per_s'], local_notice_payload=opts['notice_budget_bytes_per_s'])
            amounts = dict(cost['measurement_upper_bytes'], api_serialized=cost['api_serialized_bytes'], local_notice_payload=cost['local_notice_payload_bytes'])
            excess = sum(max(amounts[k] / (8 * limits[k]) - 1, 0) for k in ('e1', 'e2', 'e3', 'export', 'api_serialized', 'local_notice_payload'))
            near(float(base[c['name']]['normalized_excess_upper']), excess, '实际逐层归一超限独立来源')
        count += 6
        decisions = [d for d in rows(directory / 'decisions.jsonl') if 'view' in d]
        rs = [r for r in available if r['name'] == c['name']]
        bs = [r for r in batches if r['name'] == c['name']]
        ns = [r for r in notices if r['name'] == c['name']]
        same(len(bs), len(decisions), '完整批次表')
        same(len(rs), sum(d['availability']['reports_after'] - d['availability']['reports_before'] for d in decisions), '进入视图的合法报告分母')
        physical = rows(directory / 'collector.jsonl')
        for r in rs:
            actual = next(x for x in physical if x.get('ether_type') == 0x88b6 and str(x['packet_id']) == r['packet_id'])
            same(int(r['received_ns']), actual['receive_monotonic_ns'], '汇总实际收报来源')
            decision = next(d for d in decisions if str(d['scheduled_offset_us']) == r['scheduled_offset_us'])
            same(int(r['entered_lower_ns']), decision['availability']['ingest_started_ns'], '进入状态的时间下界')
            same(int(r['entered_upper_ns']), decision['availability']['available_ns'], '进入状态的时间上界')
            same(int(r['first_cutoff_ns']), decision['cutoff_ns'], '首次视图截止')
            near(float(r['receipt_to_entry_upper_ms']), (int(r['entered_upper_ns']) - int(r['received_ns'])) / 1e6, '收报至可用时间')
            count += 5
        generation = load(directory / 'generation-bounds.json')
        earlier, later = [], []
        for r in rs:
            frame = next(x for x in physical if x.get('ether_type') == 0x88b6 and str(x['packet_id']) == r['packet_id'])
            for record in frame['records']:
                interval = next(x for x in generation['intervals'] if x['packet_id'] == frame['packet_id'] and x['node'] == record['switch_id'])
                obj = {'node': record['switch_id'], 'port': 2, 'queue': 0}
                earlier.append({'object_id': obj, 'generated_us': (interval['generation_upper_ns'] - c['origin_monotonic_ns']) / 1000,
                                'received_us': (int(r['entered_lower_ns']) - c['origin_monotonic_ns']) / 1000})
                later.append({'object_id': obj, 'generated_us': (interval['generation_lower_ns'] - c['origin_monotonic_ns']) / 1000,
                              'received_us': (int(r['entered_upper_ns']) - c['origin_monotonic_ns']) / 1000})
        bounds = load(directory / 'controller-visible-bounds.json')
        for side, sample in (('lower', earlier), ('upper', later)):
            loss = clipped_loss(load(directory / 'tasks.json'), sample, 4)[0]
            near(bounds['loss_' + side], loss, '控制器视图损失独立重建')
            near(float(case_rows[c['name']]['controller_loss_' + side]), loss, '视图损失汇总来源')
            count += 2
        for b, d in zip(bs, decisions):
            e = d['availability']
            near(float(b['read_ms']), (e['read_finished_ns'] - e['read_started_ns']) / 1e6, '完整读取耗时')
            near(float(b['ingest_ms']), (e['available_ns'] - e['ingest_started_ns']) / 1e6, '状态更新耗时')
            count += 2
        actual_notices = rows(directory / 'notices.jsonl')
        same(len(ns), len(actual_notices), '完整通知表')
        for n, actual in zip(ns, actual_notices):
            same(int(n['recv_finished_ns']), actual['received_ns'], '通知实际可用')
            first = next(d for d in decisions if any(v['version'] == actual['message']['version'] for v in d['view']['notifications']))
            same(int(n['first_cycle_offset_us']), first['scheduled_offset_us'], '通知首次进入周期')
            count += 2
        first_cycle = bool(ns and int(ns[0]['first_cycle_offset_us']) == 0)
        same(case_rows[c['name']]['initial_notice_first_cycle'], str(first_cycle), '首周期通知分组')
        count += 4
    groups = {(c['scenario'], c['budget_level'], (c['method'], c['parameter_index'])) for c in cases}
    same({(r['scenario'], r['budget_level'], (r['method'], int(r['parameter_index']) if r['parameter_index'] else None)) for r in aggregate}, groups, '完整汇总单元')
    same(len(aggregate), len(groups), '无重复汇总单元')
    for a in aggregate:
        members = [c['name'] for c in cases if (c['scenario'], c['budget_level'], (c['method'], c['parameter_index'])) == (a['scenario'], a['budget_level'], (a['method'], int(a['parameter_index']) if a['parameter_index'] else None))]
        same(int(a['case_count']), len(members), '完整案例独立重复分母')
        same(int(a['business_seed_count']), len({c['seed'] for c in cases if c['name'] in members}), '真实业务种子分母')
        lower, upper = [float(base[n]['loss_lower']) for n in members], [float(base[n]['loss_upper']) for n in members]
        near(float(a['loss_lower_mean']), sum(lower) / len(lower), '损失下界均值')
        near(float(a['loss_upper_mean']), sum(upper) / len(upper), '损失上界均值')
        near(float(a['loss_observed_lower_min']), min(lower), '最小损失界')
        near(float(a['loss_observed_upper_max']), max(upper), '最大损失界')
        midpoints = [(x + y) / 2 for x, y in zip(lower, upper)]
        near(float(a['loss_midpoint_case_range']), max(midpoints) - min(midpoints), '案例间变异')
        for side in ('lower', 'upper'):
            near(float(a['controller_loss_' + side + '_mean']), sum(float(case_rows[n]['controller_loss_' + side]) for n in members) / len(members), '完整案例视图损失均值')
        same(int(a['within_cases']), sum(base[n]['window_feasibility'] == 'within' for n in members), '达标案例分母')
        same(int(a['initial_notice_first_cycle_cases']), sum(case_rows[n]['initial_notice_first_cycle'] == 'True' for n in members), '通知分组分母')
        count += 9
    count += check_pairs(output, cases, base)
    return {'status': 'passed', 'checks': count, 'cases': len(cases), 'groups': len(groups), 'report_rows': len(available)}


def check_pairs(output, cases, base):
    pairs, businesses, repeats = [load(output/name) for name in ('paired-cases.json','business-pairs.json','deployment-repeats.json')]
    from prepare_network_comparison import METHODS
    plan = load(output/'comparison-plan.json')
    seeds = plan['fresh_business_seeds']
    identities = {(c['scenario'],c['budget_level'],c['seed'],c['block'],c['method']):c for c in cases}
    same(len(identities),256,'完整身份无重复')
    same(len(pairs),224,'完整动态/静态配对')
    same(len(businesses),112,'业务组分母')
    same(len(repeats),128,'部署重复组分母')
    seen, count = set(), 4
    for p in pairs:
        key = tuple(p[k] for k in ('scenario','budget_level','seed','block','method'))
        if key in seen or p['method']=='static': raise ValueError('重复或非法配对')
        seen.add(key)
        dynamic = identities[key]
        static = identities[key[:-1]+('static',)]
        same(p['dynamic_case'],dynamic['name'],'动态配对身份')
        same(p['static_case'],static['name'],'静态配对身份')
        a,z = base[dynamic['name']],base[static['name']]
        same(p['both_within'],a['window_feasibility']==z['window_feasibility']=='within','双方预算资格')
        for observer,file in (('collector','generation-bounds.json'),('controller','controller-visible-bounds.json')):
            u,v = load(output/dynamic['name']/file),load(output/static['name']/file)
            lo,hi=u['loss_lower']-v['loss_upper'],u['loss_upper']-v['loss_lower']
            same(p[observer+'_difference'],{'lower':lo,'upper':hi,'direction':'lower' if hi<0 else 'higher' if lo>0 else 'overlap'},'条件配对差值')
        for prefix,row in (('dynamic',a),('static',z)):
            for field,value in p[prefix+'_fee_rates'].items():
                if value is None: same(row[field],'','未知费用仍为未知')
                else: near(float(row[field]),value,'配对逐层费用来源')
        count+=17
    for p in businesses:
        members=[r for r in pairs if all(r[k]==p[k] for k in ('scenario','budget_level','seed','method'))]
        same({r['block'] for r in members},{1,2},'业务内两块')
        same(len(members),2,'每业务两个配对')
        same(p['deployment_pairs'],2,'业务配对分母')
        same(p['both_within_pairs'],sum(r['both_within'] for r in members),'业务预算资格分母')
        same(p['both_blocks_eligible_lower'],all(r['both_within'] and r['collector_difference']['upper']<0 for r in members),'两块方向与资格')
        for observer in ('collector','controller'):
            same(p[observer+'_mean_difference'],[sum(r[observer+'_difference'][side] for r in members)/2 for side in ('lower','upper')],'业务内条件差值均值')
        count+=7
    from diagnose_static_transfer import signature
    signatures = {c['name']:signature(output/c['name']) for c in cases}
    seen_repeats=set()
    for p in repeats:
        key=tuple(p[k] for k in ('scenario','budget_level','seed','method'))
        if key in seen_repeats: raise ValueError('重复部署组')
        seen_repeats.add(key)
        members=[identities[key[:3]+(block,key[3])] for block in (1,2)]
        same(p['cases'],[c['name'] for c in members],'真实两部署身份')
        signatures_pair=[signatures[c['name']] for c in members]
        same(signatures_pair[0][0],signatures_pair[1][0],'部署业务一致')
        positions=[r[1] for r in signatures_pair]
        same(p['same_sampling_positions'],positions[0]==positions[1],'重复采样位置独立来源')
        same(p['position_symmetric_difference'],len(set(positions[0])^set(positions[1])),'重复位置差异')
        values=[[float(base[c['name']]['loss_'+side]) for side in ('lower','upper')] for c in members]
        same(p['loss_bounds'],values,'部署条件损失来源')
        same(p['loss_intervals_overlap'],max(x[0] for x in values)<=min(x[1] for x in values),'重复条件界重叠')
        near(p['loss_midpoint_range'],abs(sum(values[0])-sum(values[1]))/2,'业务内部署变异')
        count+=7
    groups=load(output/'paired-groups.json')
    same(len(groups),28,'四场景七动态方法')
    for p in groups:
        members=[r for r in businesses if all(r[k]==p[k] for k in ('scenario','budget_level','method'))]
        same({r['seed'] for r in members},set(seeds),'真实独立业务分母')
        same(p['business_count'],4,'四业务不是八部署')
        same(p['both_within_pairs'],sum(r['both_within_pairs'] for r in members),'全部预算配对资格')
        same(p['both_blocks_eligible_lower_businesses'],sum(r['both_blocks_eligible_lower'] for r in members),'改善业务分母')
        same(p['collector_mean_difference'],[sum(r['collector_mean_difference'][i] for r in members)/4 for i in (0,1)],'业务间汇总分母')
        count+=5
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    output = parser.parse_args().analysis
    audit(output)
    manifest = load(output / 'manifest.json')
    for name, digest in manifest['analysis_source_sha256'].items():
        same(sha(output / 'executed-analysis-source' / name), digest, '分析实际源码')
    print(json.dumps(check_summary(output, manifest['cases']), ensure_ascii=False))
