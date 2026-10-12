"""核对网络静态选择与冻结模型参照的同业务采样位置和年龄差。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def signature(directory):
    packets = load(directory/'packets.json')
    ids = {p['packet_id']:i for i,p in enumerate(packets)}
    if len(ids) != len(packets):
        raise ValueError('离线包序号不唯一')
    workload = [{k:p[k] for k in ('flow','offset_ns','ip_length','path')} for p in packets]
    samples = []
    for report in rows(directory/'collector.jsonl'):
        if report.get('ether_type') != 0x88b6:
            continue
        if report['packet_id'] not in ids:
            raise ValueError('报告没有对应业务包')
        index = ids[report['packet_id']]
        for r in report['records']:
            if packets[index]['path'][r['hop']-1] != r['switch_id']:
                raise ValueError('报告位置不在实际管理路径')
            samples.append((index,packets[index]['flow'],r['switch_id'],r['hop']))
    if len(set(samples)) != len(samples):
        raise ValueError('普通 K=1 训练存在重复采样位置')
    return workload, sorted(samples)


def diagnose(analysis, output):
    m = load(analysis/'manifest.json')
    if m['status'] != 'passed' or m['smoke'] or len(m['cases']) != 192:
        raise ValueError('要求完整有限静态训练')
    required = ['candidate-scores.json','network-selections.json','summary-checks.json']
    for c in m['cases']:
        required += [c['name']+'/'+name for name in ('packets.json','collector.jsonl','tasks.json','generation-bounds.json')]
    for name in required:
        if sha(analysis/name) != m['artifact_sha256'][name]:
            raise ValueError('诊断输入摘要不同')
    scores, selections = load(analysis/'candidate-scores.json'), load(analysis/'network-selections.json')
    pairs = []
    for s in selections:
        if s['selected_candidate'] is None:
            continue
        for seed in (11,29):
            selected = next(c for c in m['cases'] if (c['scenario'],c['seed'],c['candidate_index'])==(s['scenario'],seed,s['selected_candidate']))
            model = next(c for c in m['cases'] if (c['scenario'],c['seed'],c['candidate_index'])==(s['scenario'],seed,s['model_candidate']))
            a,b = analysis/selected['name'],analysis/model['name']
            wa,sa = signature(a)
            wb,sb = signature(b)
            if wa != wb or load(a/'tasks.json') != load(b/'tasks.json'):
                raise ValueError('模型与网络配置不使用共同业务或任务')
            aa,ab = load(a/'generation-bounds.json'),load(b/'generation-bounds.json')
            pairs.append({'scenario':s['scenario'],'budget_level':s['budget_level'],'seed':seed,
                'selected_case':selected['name'],'model_case':model['name'],
                'selected_candidate':s['selected_candidate'],'model_candidate':s['model_candidate'],
                'same_sampling_positions':sa==sb,'selected_sample_count':len(sa),'model_sample_count':len(sb),
                'position_symmetric_difference':len(set(sa)^set(sb)),
                'selected_loss_bounds':[aa['loss_lower'],aa['loss_upper']],
                'model_loss_bounds':[ab['loss_lower'],ab['loss_upper']],
                'loss_intervals_overlap':max(aa['loss_lower'],ab['loss_lower'])<=min(aa['loss_upper'],ab['loss_upper']),
                'selected_upper_minus_model_upper':aa['loss_upper']-ab['loss_upper'],
                'selected_positions':sa,'model_positions':sb})
    migration = []
    for s in selections:
        model = next(r for r in scores if r['scenario']==s['scenario'] and r['frozen_model_reference'])
        mean_lower = sum(b[0] for b in model['collector_loss_bounds'].values())/2
        migration.append({'scenario':s['scenario'],'budget_level':s['budget_level'],
            'model_training_mean_loss':model['model_score'][1],
            'network_model_mean_loss_bounds':[mean_lower,s['model_rank'][1]],
            'network_selected_worst_upper':s['selected_rank'][0],
            'network_model_worst_upper':s['model_rank'][0],
            'worst_upper_difference':s['selected_rank'][0]-s['model_rank'][0]})
    output.mkdir(parents=True,exist_ok=False)
    def write(name,value):
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    write('sampling-position-pairs.json',pairs)
    write('model-network-migration.json',migration)
    findings = {'status':'passed','pairs':len(pairs),'changed_config_pairs':sum(r['selected_candidate']!=r['model_candidate'] for r in pairs),
                'same_sampling_position_pairs':sum(r['same_sampling_positions'] for r in pairs),
                'overlapping_age_interval_pairs':sum(r['loss_intervals_overlap'] for r in pairs),
                'scope':'同已消费训练业务，离线包序号仅用于验证；相同位置不证明一般等价，微小差距不归为新增采样机会收益'}
    write('findings.json',findings)
    source = Path(__file__).resolve()
    target = output/'executed-source/experiments'/source.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(source,target)
    write('manifest.json',{'status':'passed','analysis_manifest_sha256':sha(analysis/'manifest.json'),
          'input_sha256':{name:sha(analysis/name) for name in required},'source_sha256':{'experiments/'+source.name:sha(source)},
          'findings':findings,'artifact_sha256':{str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}})
    print(json.dumps(findings,ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--analysis',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a = p.parse_args()
    diagnose(a.analysis,a.output)
