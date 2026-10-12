"""展示完整独立业务配对、两观察位置及六层费用预算比例。"""
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

METHODS = ('static','feedback','local','joint','phase_joint','uniform_guard','phase_guard','phase_local_guard')
LABELS = ('Static','Feedback','Local','Joint','Phase joint','Uniform guard','Phase guard','Phase local')
PROFILES = (('stable','high','Stable / high'),('shared_priority','high','Priority / high'),
            ('switch','low','Notified switch / low'),('bursty_jitter','low','Jitter / low'))
COLORS = ('#333333','#27749c','#b16b1f','#278359')


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def plot(analysis, output):
    manifest=json.loads((analysis/'manifest.json').read_text())
    if manifest['status']!='passed' or len(manifest['cases'])!=256:
        raise ValueError('要求完整独立业务比较')
    names=('summary.csv','case-availability.csv','paired-cases.json','business-pairs.json',
           'paired-groups.json','deployment-repeats.json','method-contract.json','summary-checks.json')
    for name in names:
        if sha(analysis/name)!=manifest['artifact_sha256'][name]: raise ValueError('比较图源摘要不同')
    if json.loads((analysis/'summary-checks.json').read_text())['status']!='passed': raise ValueError('分析未独立通过')
    pairs=json.loads((analysis/'paired-cases.json').read_text())
    contract=json.loads((analysis/'method-contract.json').read_text())
    seeds=manifest['fresh_business_seeds']
    if len(seeds)!=4 or len(pairs)!=224: raise ValueError('绘图业务或配对分母错误')
    with (analysis/'summary.csv').open() as stream: summaries=list(csv.DictReader(stream))
    with (analysis/'case-availability.csv').open() as stream: views={r['name']:r for r in csv.DictReader(stream)}
    fp=Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(fp))
    font=font_manager.FontProperties(fname=fp)
    if font.get_name()!='Times New Roman' or Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'),fallback_to_default=False)).resolve()!=fp.resolve():
        raise ValueError('Times New Roman字体解析失败')
    plt.rcParams.update({'font.family':'Times New Roman','font.size':9,'pdf.fonttype':42,'axes.unicode_minus':False})
    output.mkdir(parents=True,exist_ok=False)
    def finish(fig,name,title):
        fig.savefig(output/(name+'.png'),dpi=300,metadata={'Software':'Matplotlib'})
        fig.savefig(output/(name+'.pdf'),metadata={'Author':'','Creator':'','Title':title})
        plt.close(fig)
        fonts=subprocess.check_output(['pdffonts',str(output/(name+'.pdf'))],text=True)
        if not fonts.splitlines()[2:] or any('TimesNewRoman' not in r or ' yes ' not in r for r in fonts.splitlines()[2:]): raise ValueError('PDF字体嵌入错误')
        (output/(name+'-fonts.txt')).write_text(fonts)

    fig,axes=plt.subplots(4,1,figsize=(9,10))
    for axis,(s,b,label) in zip(axes,PROFILES):
        for i,method in enumerate(METHODS[1:]):
            selected=[p for p in pairs if (p['scenario'],p['budget_level'],p['method'])==(s,b,method)]
            if len(selected)!=8: raise ValueError('配对展示缺少业务或部署')
            for p in selected:
                j=seeds.index(p['seed']);x=i+(j-1.5)*.14+(p['block']-1.5)*.045
                lo,hi=p['collector_difference']['lower'],p['collector_difference']['upper'];mid=(lo+hi)/2
                axis.errorbar(x,mid,yerr=[[mid-lo],[hi-mid]],fmt='o' if p['block']==1 else 's',
                    color=COLORS[j],fillstyle='full' if p['block']==1 else 'none',markersize=3.5,capsize=2,elinewidth=.7)
                if not p['both_within']: axis.plot(x,mid,'x',color='#b32d2d',markersize=6)
        axis.axhline(0,color='#777777',linewidth=.7,linestyle='--')
        axis.set_title(label);axis.set_xticks(range(7),LABELS[1:]);axis.set_xlim(-.5,6.5)
        axis.set_ylabel('Collector age-loss difference');axis.grid(axis='y',color='#dddddd',linewidth=.4);axis.set_axisbelow(True)
    handles=[plt.Line2D([],[],marker='o',color=c,linestyle='None',label=f'Seed {seed}') for seed,c in zip(seeds,COLORS)]
    handles+=[plt.Line2D([],[],marker=m,color='#777777',fillstyle=f,linestyle='None',label=l) for m,f,l in
              (('o','full','Block 1'),('s','none','Block 2'),('x','full','At least one budget failure'))]
    handles[-1].set_color('#b32d2d')
    fig.legend(handles=handles,loc='upper center',ncol=4,frameon=False,fontsize=8)
    fig.text(.5,.01,'All 224 paired cases. Dynamic minus static. Conditional intervals, not confidence intervals.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.035,1,.94),pad=1)
    finish(fig,'comparison-paired-age','All independent-business paired age differences with budget eligibility')

    fig,axes=plt.subplots(2,4,figsize=(12,6.5),sharey=True)
    for col,(s,b,label) in enumerate(PROFILES):
        for row,observer in enumerate(('collector','controller')):
            axis=axes[row,col]
            for i,method in enumerate(METHODS):
                rs=[r for r in summaries if (r['scenario'],r['budget_level'],r['method'])==(s,b,method)]
                if len(rs)!=8: raise ValueError('双观察位置完整案例分母错误')
                source=rs if observer=='collector' else [views[r['case']] for r in rs]
                prefix='' if observer=='collector' else 'controller_'
                lo=sum(float(r[prefix+'loss_lower']) for r in source)/8;hi=sum(float(r[prefix+'loss_upper']) for r in source)/8;mid=(lo+hi)/2
                axis.errorbar(i,mid,yerr=[[mid-lo],[hi-mid]],fmt='D' if method=='static' else 'o',color='#333333' if method=='static' else '#27749c',markersize=4,capsize=3)
                failed=sum(r['window_feasibility']!='within' for r in rs)
                if failed: axis.plot(i,mid,'x',color='#b32d2d',markersize=8);axis.annotate(f'{failed}/8',(i,mid),xytext=(0,8),textcoords='offset points',ha='center',fontsize=7)
            axis.set_title(label+' / '+observer);axis.set_xticks(range(8),LABELS,rotation=55,ha='right',fontsize=8)
            axis.set_ylim(0,4.2);axis.set_xlim(-.5,7.5);axis.grid(axis='y',color='#dddddd',linewidth=.4);axis.set_axisbelow(True)
            if col==0: axis.set_ylabel('Clipped normalized age loss')
    fig.text(.5,.012,'Four independent businesses, each with two deployments. Conditional means, not confidence intervals. Red crosses: budget failures.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.065,1,1),pad=1)
    finish(fig,'comparison-observers','Collector and controller age for all frozen methods on independent businesses')

    fig,axes=plt.subplots(1,2,figsize=(8,4))
    selected=[p for p in pairs if p['scenario']=='shared_priority' and p['method']=='phase_guard']
    if len(selected)!=8 or any(not p['both_within'] for p in selected):
        raise ValueError('重点保护观察位置图完整配对与资格错误')
    for axis,observer,label in zip(axes,('collector','controller'),('Collector','Controller view')):
        for p in selected:
            j=seeds.index(p['seed']);lo,hi=p[observer+'_difference']['lower'],p[observer+'_difference']['upper'];mid=(lo+hi)/2
            axis.errorbar(j+(p['block']-1.5)*.12,mid,yerr=[[mid-lo],[hi-mid]],fmt='o' if p['block']==1 else 's',
                color=COLORS[j],fillstyle='full' if p['block']==1 else 'none',markersize=4,capsize=3,elinewidth=.8)
        axis.axhline(0,color='#777777',linewidth=.7,linestyle='--')
        axis.set_title('Priority / high: '+label);axis.set_xticks(range(4),[str(s) for s in seeds])
        axis.set_xlabel('Business seed');axis.set_ylabel('Age-loss difference');axis.set_xlim(-.4,3.4)
        axis.grid(axis='y',color='#dddddd',linewidth=.4);axis.set_axisbelow(True)
    fig.text(.5,.015,'Frozen Phase guard P02 minus static. Two blocks per business. Conditional intervals, not confidence intervals.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.065,1,1),pad=1)
    finish(fig,'comparison-priority-observation-boundary','Separate collector and controller differences of frozen Phase guard on priority tasks')

    fig,axes=plt.subplots(2,3,figsize=(11,7))
    for axis,layer,title in zip(axes.flat,('e1','e2','e3','export','api','local_notice'),
                               ('In-band e1','In-band e2','In-band e3','Export attempts','Serialized API','Local notice payload')):
        for (s,b,label),color in zip(PROFILES,COLORS):
            lows,highs,means=[],[],[]
            for method in METHODS:
                rs=[r for r in summaries if (r['scenario'],r['budget_level'],r['method'])==(s,b,method)]
                fields=[layer+'_'+side+'_bytes_per_s' for side in ('lower','upper')] if layer in ('e1','e2','e3','export') else [layer+'_bytes_per_s']*2
                if any(r[field]=='' for r in rs for field in fields): raise ValueError('不可评价费用需单独处理，不能作零费用图')
                cap=contract['budget'][b][layer]
                lo=[float(r[fields[0]])/cap for r in rs];hi=[float(r[fields[1]])/cap for r in rs]
                lows.append(min(lo));highs.append(max(hi));means.append(sum((a+z)/2 for a,z in zip(lo,hi))/8)
            axis.errorbar(range(8),means,yerr=[[max(m-l,0) for m,l in zip(means,lows)],[max(h-m,0) for m,h in zip(means,highs)]],fmt='o-',color=color,markersize=3,capsize=2,linewidth=.7,label=label)
        axis.axhline(1,color='#b32d2d',linewidth=.8,linestyle='--');axis.set_title(title)
        axis.set_xticks(range(8),LABELS,rotation=55,ha='right',fontsize=8);axis.set_ylabel('Observed cost / budget')
        axis.set_ylim(bottom=0);axis.grid(axis='y',color='#dddddd',linewidth=.4);axis.set_axisbelow(True)
    fig.legend(handles=axes.flat[0].get_legend_handles_labels()[0],loc='upper center',ncol=4,frameon=False,fontsize=8)
    fig.text(.5,.01,'Six separate layers. Markers: eight-case means. Bars: all-case conditional ranges. Dashed lines: budget limits.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.055,1,.94),pad=1)
    finish(fig,'comparison-cost-layers','Separate physical and control cost layers for all independent-business deployments')
    for name in names: shutil.copyfile(analysis/name,output/name)
    source=Path(__file__).resolve();target=output/'executed-source/experiments'/source.name;target.parent.mkdir(parents=True);shutil.copyfile(source,target)
    record={'status':'passed','analysis_manifest_sha256':sha(analysis/'manifest.json'),
        'input_sha256':{n:sha(analysis/n) for n in names},'source_sha256':{'experiments/'+source.name:sha(source)},
        'font_name':font.get_name(),'font_file':str(fp),'font_sha256':sha(fp),'dpi':300,
        'scope':'256案例与224配对完整保留，四业务与部署重复分开，条件区间非置信区间',
        'artifact_sha256':{str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}}
    (output/'manifest.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();plot(a.analysis,a.output)
