"""保留全部有限候选预算状态，并分列采集端与控制器年龄。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

PROFILES = (('stable','high','Stable / high'), ('shared_priority','high','Priority / high'),
            ('switch','low','Notified switch / low'), ('bursty_jitter','low','Jitter / low'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--analysis',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a = p.parse_args()
    m = json.loads((a.analysis/'manifest.json').read_text())
    if m['status'] != 'passed' or m['smoke'] or len(m['cases']) != 192:
        raise ValueError('图源必须为完整 192 例有限静态训练')
    names = ('candidate-scores.json','network-selections.json','block-aggregate.csv','summary-checks.json')
    for name in names:
        if sha(a.analysis/name) != m['artifact_sha256'][name]:
            raise ValueError('图源摘要不同')
    if json.loads((a.analysis/'summary-checks.json').read_text())['status'] != 'passed':
        raise ValueError('图源未通过独立核对')
    scores = json.loads((a.analysis/'candidate-scores.json').read_text())
    selections = json.loads((a.analysis/'network-selections.json').read_text())
    with (a.analysis/'block-aggregate.csv').open() as f:
        aggregate = list(csv.DictReader(f))
    fp = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(fp))
    font = font_manager.FontProperties(fname=fp)
    if font.get_name() != 'Times New Roman' or Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'),fallback_to_default=False)).resolve() != fp.resolve():
        raise ValueError('Times New Roman 字体解析失败')
    plt.rcParams.update({'font.family':'Times New Roman','font.size':9,'pdf.fonttype':42,'axes.unicode_minus':False})
    a.output.mkdir(parents=True,exist_ok=False)
    def finish(fig,name,title):
        fig.savefig(a.output/(name+'.png'),dpi=300,metadata={'Software':'Matplotlib'})
        fig.savefig(a.output/(name+'.pdf'),metadata={'Author':'','Creator':'','Title':title})
        plt.close(fig)
        info = subprocess.run(['pdffonts',str(a.output/(name+'.pdf'))],capture_output=True,text=True,check=True).stdout
        if not info.splitlines()[2:] or any('TimesNewRoman' not in line or ' yes ' not in line for line in info.splitlines()[2:]):
            raise ValueError('PDF 字体未正确嵌入')
        (a.output/(name+'-fonts.txt')).write_text(info)
    def selection(s,b):
        return next(r for r in selections if (r['scenario'],r['budget_level'])==(s,b))
    fig,axes = plt.subplots(2,2,figsize=(8,6),sharex=True,sharey=True)
    for axis,(s,b,label) in zip(axes.flat,PROFILES):
        rs = [r for r in scores if (r['scenario'],r['budget_level'])==(s,b)]
        if len(rs) != 24:
            raise ValueError('候选图分母错误')
        winner = selection(s,b)
        for r in rs:
            i = r['candidate_index']
            if r['rank'] is None:
                axis.annotate('No age',(i,4),rotation=90,fontsize=6)
                continue
            y = r['rank'][0]
            eligible = r['feasible']
            axis.plot(i,y,'o' if eligible else 'x',color='#27749c' if eligible else '#b32d2d',markersize=3.5)
            if r['frozen_model_reference']:
                axis.plot(i,y,'D',color='#333333',fillstyle='none',markersize=7)
            if i == winner['selected_candidate']:
                axis.plot(i,y,'s',color='#ad6824',fillstyle='none',markersize=9)
        axis.set_title(label+f" ({winner['feasible_candidates']}/24 eligible)")
        axis.set_xticks(range(0,24,2))
        axis.set_xlim(-.7,23.7)
        axis.set_ylim(0,4.2)
        axis.grid(color='#dedede',linewidth=.4)
        axis.set_axisbelow(True)
    for axis in axes[1]:
        axis.set_xlabel('Frozen candidate index')
    for axis in axes[:,0]:
        axis.set_ylabel('Worst collector age-loss upper bound')
    handles = [plt.Line2D([],[],marker=mk,color=col,linestyle='None',fillstyle=fill,label=text)
               for mk,col,fill,text in [('o','#27749c','full','Eligible in both seeds'),('x','#b32d2d','full','Excluded by budget or age'),
                                        ('D','#333333','none','Frozen model reference'),('s','#ad6824','none','Network-selected candidate')]]
    fig.legend(handles=handles,loc='upper center',ncol=2,frameon=False,fontsize=8)
    fig.text(.5,.015,'Seeds 11 and 29. One case per candidate and seed. Finite 24/6561 subset; training data.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.045,1,.91),pad=.8)
    finish(fig,'static-candidates-age-budget','Finite static candidates with all budget exclusions retained')
    fig,axes = plt.subplots(1,2,figsize=(8,4.5),sharey=True)
    for axis,observer in zip(axes,('collector','controller')):
        for i,(s,b,label) in enumerate(PROFILES):
            r = selection(s,b)
            for offset,index,display,color,marker in ((-.12,r['model_candidate'],'Frozen model','#333333','D'),
                                                       (.12,r['selected_candidate'],'Network selected','#27749c','o')):
                if index is None:
                    axis.text(4,i+offset,'No feasible candidate',ha='right',fontsize=7)
                    continue
                group = next(v for v in aggregate if (v['scenario'],v['budget_level'],int(v['candidate_index']))==(s,b,index))
                lo = float(group['loss_lower_mean' if observer=='collector' else 'controller_loss_lower_mean'])
                hi = float(group['loss_upper_mean' if observer=='collector' else 'controller_loss_upper_mean'])
                mid = (lo+hi)/2
                axis.errorbar(mid,i+offset,xerr=[[mid-lo],[hi-mid]],fmt=marker,color=color,markersize=4,elinewidth=1,capsize=2,label=display if i==0 else None)
                if int(group['within_cases']) != 2:
                    axis.plot(mid,i+offset,'x',color='#b32d2d',markersize=7)
        axis.set_title('Collector receipt' if observer=='collector' else 'Controller view')
        axis.set_xlabel('Clipped normalized age loss')
        axis.set_yticks(range(4),[r[2] for r in PROFILES])
        axis.set_xlim(0,4.1)
        axis.grid(axis='x',color='#dedede',linewidth=.4)
        axis.set_axisbelow(True)
    axes[0].invert_yaxis()
    handles,labels = axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='upper center',ncol=2,frameon=False,fontsize=8)
    fig.text(.5,.015,'Two-seed mean conditional intervals, not confidence intervals. Red crosses mark budget exclusions.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.06,1,.91),pad=.8)
    finish(fig,'static-model-network-observers','Collector and controller age for model and network static selections')
    for name in names[:3]:
        shutil.copyfile(a.analysis/name,a.output/name)
    source = Path(__file__).resolve()
    target = a.output/'executed-source/experiments'/source.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(source,target)
    record = {'status':'passed','analysis_manifest_sha256':sha(a.analysis/'manifest.json'),
              'input_sha256':{n:sha(a.analysis/n) for n in names},'source_sha256':{'experiments/'+source.name:sha(source)},
              'font_name':font.get_name(),'font_file':str(fp),'font_sha256':sha(fp),'dpi':300,
              'scope':'有限训练候选，全部排除点保留，两观察位置分列；非新业务性能比较',
              'artifact_sha256':{str(p.relative_to(a.output)):sha(p) for p in a.output.rglob('*') if p.is_file()}}
    (a.output/'manifest.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')


if __name__ == '__main__':
    main()
