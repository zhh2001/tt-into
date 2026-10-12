"""绘制独立业务的两位置年龄、重点监测取舍和部署观察变化。"""
import argparse
import csv
from pathlib import Path
import shutil
import statistics
import subprocess
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
import json

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')

from check_matched_network import load,sha
from literature_comparison_identity import METHODS

ROOT=Path(__file__).resolve().parents[1]
NAMES=('controller-independent-comparison','collector-independent-comparison','priority-business-tradeoff','observer-deployment-diagnostics')
METHOD_LABELS=('Static','Feedback','Local','Joint','Phase joint','Uniform guard','Phase guard','Phase local guard','INTOPP-TT','D-INTO-TT')
PROFILES=('stable','shared_priority','switch','bursty_jitter')
LABELS=('Stable / high','Priority / high','Switch / low','Bursty jitter / low')


def plot(analysis,checks,output):
    audit=load(checks);m=load(analysis/'manifest.json')
    if audit['status']!='passed' or audit['cases']!=320 or audit['analysis_manifest_sha256']!=sha(analysis/'manifest.json'):
        raise ValueError('需要完整320例独立验收')
    inputs=('summary.csv','paired-cases.json','business-pairs.json','paired-groups.json','deployment-repeats.json','report-availability.csv','stage-plan.json')
    for name in inputs:
        if sha(analysis/name)!=m['artifact_sha256'][name]:raise ValueError('图源改变')
    table=list(csv.DictReader((analysis/'summary.csv').open()))
    pairs=load(analysis/'paired-cases.json');businesses=load(analysis/'business-pairs.json')
    groups=load(analysis/'paired-groups.json');repeats=load(analysis/'deployment-repeats.json')
    available=list(csv.DictReader((analysis/'report-availability.csv').open()))
    methods=load(analysis/'method-contract.json')['reference'];seeds=load(analysis/'stage-plan.json')['business_seeds']
    fp=Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(fp));font=font_manager.FontProperties(fname=fp)
    if font.get_name()!='Times New Roman' or Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'),fallback_to_default=False)).resolve()!=fp.resolve():
        raise ValueError('真实TNR解析失败')
    plt.rcParams.update({'font.family':'Times New Roman','font.size':9,'pdf.fonttype':42,'axes.unicode_minus':False})
    output.mkdir(parents=True,exist_ok=False)
    def finish(fig,name,title):
        fig.savefig(output/(name+'.png'),dpi=300,metadata={'Software':'Matplotlib'})
        fig.savefig(output/(name+'.pdf'),metadata={'Author':'','Creator':'','Title':title});plt.close(fig)
        fonts=subprocess.check_output(['pdffonts',str(output/(name+'.pdf'))],text=True)
        if not fonts.splitlines()[2:] or any('TimesNewRoman' not in r or ' yes ' not in r for r in fonts.splitlines()[2:]):raise ValueError('PDF字体未嵌入')
        (output/(name+'-fonts.txt')).write_text(fonts)
    colors=('#326b90','#ac642f')
    for observer in ('controller','collector'):
        fig,axes=plt.subplots(2,2,figsize=(10.2,7.0))
        for axis,scenario,label in zip(axes.flat,PROFILES,LABELS):
            for i,method in enumerate(METHODS):
                values=[r for r in table if (r['scenario'],r['method'])==(scenario,method)]
                lo,hi=[statistics.mean(float(r[observer+'_loss_'+s]) for r in values) for s in ('lower','upper')]
                axis.errorbar(i,(lo+hi)/2,yerr=[[(hi-lo)/2],[(hi-lo)/2]],fmt='o',color=colors[0],capsize=3,markersize=4)
                for j,r in enumerate(sorted(values,key=lambda r:(int(r['seed']),int(r['block'])))):
                    y=(float(r[observer+'_loss_lower'])+float(r[observer+'_loss_upper']))/2
                    x=i+(j-3.5)*.045
                    axis.scatter([x],[y],color=colors[int(r['block'])-1],s=7,alpha=.55,zorder=2)
                    if r['window_feasibility']!='within':axis.scatter([x],[y],marker='x',color='#b03125',s=25,zorder=4)
            axis.set_title(label);axis.set_xticks(range(10),METHOD_LABELS,rotation=45,ha='right')
            axis.set_xlim(-.5,9.5);axis.set_ylabel(observer.title()+' normalized age loss')
            axis.grid(axis='y',color='#dddddd',linewidth=.4);axis.set_axisbelow(True)
        fig.legend(handles=[Line2D([],[],marker='o',color=colors[0],linestyle='none',label='Block 1'),
            Line2D([],[],marker='o',color=colors[1],linestyle='none',label='Block 2'),
            Line2D([],[],marker='x',color='#b03125',linestyle='none',label='Exceeded or unavailable window budget')],
            loc='upper center',ncol=3,frameon=False,fontsize=8)
        fig.text(.5,.012,'Four independent businesses, two deployments each. Bars show mean conditional bounds, not confidence intervals.',ha='center',fontsize=8)
        fig.tight_layout(rect=(0,.04,1,.94));finish(fig,observer+'-independent-comparison',observer.title()+' age loss on independent businesses')
    fig,axes=plt.subplots(1,2,figsize=(10.2,4.1))
    chosen=('phase_joint','intopp_due','d_into_tt')
    hues=('#ac642f','#4f7a49','#865c8b')
    labels=('Phase joint','INTOPP-TT','D-INTO-TT')
    for k,(method,hue,label) in enumerate(zip(chosen,hues,labels)):
        values=[p for p in pairs if (p['scenario'],p['method'])==('shared_priority',method)]
        for i,seed in enumerate(seeds):
            ps=sorted([p for p in values if p['seed']==seed],key=lambda p:p['block'])
            center=i+(k-1)*.23
            for j,p in enumerate(ps):
                x=center+(j-.5)*.06
                axes[0].scatter(x,100*p['controller_midpoint_relative_reduction'],color=hue,marker='o' if j==0 else 's',s=18,label=label if i==0 and j==0 else None)
                if not p['both_within']:axes[0].scatter(x,100*p['controller_midpoint_relative_reduction'],marker='x',color='#b03125',s=35)
            b=next(r for r in businesses if (r['scenario'],r['method'],r['seed'])==('shared_priority',method,seed))
            axes[0].plot([center-.07,center+.07],[100*b['controller_midpoint_relative_reduction']]*2,color=hue,linewidth=1.3)
    axes[0].axhline(0,color='#777777',linewidth=.7);axes[0].set_xticks(range(4),[str(s) for s in seeds])
    axes[0].set_xlabel('Independent business');axes[0].set_ylabel('Controller age-loss reduction (%)')
    axes[0].set_title('Priority scenario, paired with static');axes[0].legend(frameon=False,fontsize=8)
    layers=('e1','e2','e3','export','api_serialized','local_notice_payload');layer_labels=('Link 1','Link 2','Link 3','Export','API','Notice')
    costs=[('static',colors[0],'Static')]+list(zip(chosen,hues,labels))
    for j,(method,hue,label) in enumerate(costs):
        g=next(g for g in groups if (g['scenario'],g['method'])==('shared_priority',chosen[0] if method=='static' else method))
        for i,layer in enumerate(layers):
            bounds=g[('static' if method=='static' else 'dynamic')+'_mean_fee_rates'][layer]
            limit=methods['budget']['high'][{'api_serialized':'api','local_notice_payload':'local_notice'}.get(layer,layer)]
            if all(v is not None for v in bounds):axes[1].bar(i+(j-1.5)*.2,sum(bounds)/2/limit,width=.19,color=hue,label=label if i==0 else None)
            else:axes[1].text(i+(j-1.5)*.2,.05,'?',ha='center')
    axes[1].axhline(1,color='#777777',linestyle=':',linewidth=.8);axes[1].set_xticks(range(6),layer_labels)
    axes[1].set_ylabel('Mean cost / layer budget');axes[1].set_title('Priority scenario, six separate cost layers');axes[1].legend(frameon=False,fontsize=8)
    for axis in axes:axis.grid(axis='y',color='#dddddd',linewidth=.4);axis.set_axisbelow(True)
    fig.text(.5,.015,'Markers: deployments. Short lines: business means. Cost layers retain their respective byte definitions.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.05,1,1));finish(fig,NAMES[2],'Priority controller freshness and actual costs')
    fig,axes=plt.subplots(1,2,figsize=(10.2,4.1))
    for method,color in (('static',colors[0]),('phase_joint',colors[1]),('intopp_due','#4f7a49'),('d_into_tt','#865c8b')):
        v=sorted(float(r['receipt_to_entry_upper_ms']) for r in available if r['method']==method)
        axes[0].step(v,[(i+1)/len(v) for i in range(len(v))],where='post',label=method.replace('_',' ').title(),color=color,linewidth=1)
    axes[0].set_xlabel('Report receipt to cache entry upper bound (ms)');axes[0].set_ylabel('Empirical report fraction')
    axes[0].legend(frameon=False,fontsize=8);axes[0].set_title('Actual common report observation')
    for i,method in enumerate(METHODS):
        v=[r['controller_midpoint_range'] for r in repeats if r['method']==method]
        axes[1].scatter([i+(j-7.5)*.027 for j in range(len(v))],v,color=colors[0],s=10,alpha=.7)
    axes[1].set_xticks(range(10),METHOD_LABELS,rotation=45,ha='right');axes[1].set_ylabel('Within-business controller age-loss range')
    axes[1].set_title('Two independent network deployments')
    for axis in axes:axis.grid(axis='y',color='#dddddd',linewidth=.4);axis.set_axisbelow(True)
    fig.text(.5,.015,'Observation latency and deployment variation are measured in the BMv2 software environment.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.05,1,1));finish(fig,NAMES[3],'Actual report observation and deployment variation')
    fig,axes=plt.subplots(2,3,figsize=(10.2,6.8))
    layers=('e1','e2','e3','export','api','local_notice')
    titles=('Link 1','Link 2','Link 3','Export attempts','API serialized','Local notification payload')
    for axis,layer,title in zip(axes.flat,layers,titles):
        for index,(scenario,label) in enumerate(zip(PROFILES,LABELS)):
            values=[]
            for method in METHODS:
                rs=[r for r in table if (r['scenario'],r['method'])==(scenario,method)]
                field={'api':'api_bytes_per_s','local_notice':'local_notice_bytes_per_s'}.get(layer,layer+'_upper_bytes_per_s')
                limit=methods['budget'][rs[0]['budget_level']][layer]
                values.append(statistics.mean(float(r[field]) for r in rs)/limit if all(r[field]!='' for r in rs) else float('nan'))
            axis.plot(range(10),values,marker='o',ms=3,label=label,linewidth=.8)
        axis.axhline(1,color='#777777',linestyle=':',linewidth=.7)
        axis.set_title(title);axis.set_ylabel('Mean upper cost / layer budget')
        axis.set_xticks(range(10),METHOD_LABELS,rotation=55,ha='right',fontsize=7)
        axis.grid(axis='y',color='#dddddd',linewidth=.4)
    axes[0,0].legend(frameon=False,fontsize=7)
    fig.text(.5,.012,'Each layer uses its own byte definition. A missing value leaves a gap; individual budget states remain in the data.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.04,1,1));finish(fig,'six-layer-independent-costs','Separate actual costs for all profiles and methods')
    source='experiments/plot_literature_comparison.py';target=output/'executed-source'/source
    target.parent.mkdir(parents=True);shutil.copyfile(ROOT/source,target)
    write(output/'manifest.json',dict(status='passed',analysis_manifest_sha256=sha(analysis/'manifest.json'),
        independent_checks_sha256=sha(checks),font_name=font.get_name(),font_file=str(fp),font_sha256=sha(fp),
        source_sha256={source:sha(ROOT/source)},artifact_sha256={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('analysis','checks','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();plot(a.analysis,a.checks,a.output)
