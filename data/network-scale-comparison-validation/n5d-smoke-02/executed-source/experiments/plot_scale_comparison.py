"""六方法规模比较的完整年龄、费用点与两条规模轴。"""
import argparse
import json
from pathlib import Path
import subprocess
import math

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from scalable_contract import sha
from check_observation_wire import load

METHODS=('static','feedback','phase_joint','phase_guard','intopp_due','d_into_tt')
LABELS=('Static TT-INT','Feedback','Phase joint','Phase guard','INTOPP-TT','D-INTO-TT')
COLORS=('#555555','#C18A34','#315C8C','#8B4C8B','#438267','#BE5B49')


def plot(analysis,output):
    summary=load(analysis/'summary.json');plan=load(analysis/'stage-plan.json')
    if plan['mode']!='comparison' or len(summary)!=432:
        raise ValueError('图源必须含全部432例正式比较')
    font=Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(font))
    if Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'),fallback_to_default=False)).resolve()!=font.resolve():
        raise ValueError('未解析到真正Times New Roman')
    plt.rcParams.update({'font.family':'Times New Roman','font.size':9,'pdf.fonttype':42})
    output.mkdir(parents=True,exist_ok=False)
    networks=('n3f2','n5f2','n8f2','n5f4','n5f8');scenes=('stable','shared_priority')

    def finish(fig,name):
        fig.savefig(output/(name+'.png'),dpi=300,bbox_inches='tight')
        fig.savefig(output/(name+'.pdf'),bbox_inches='tight',metadata={'Creator':'Matplotlib','Title':name})
        plt.close(fig)
        fonts=subprocess.check_output(['pdffonts',str(output/(name+'.pdf'))],text=True)
        if not fonts.splitlines()[2:] or any('TimesNewRomanPSMT' not in line or 'yes yes yes' not in line for line in fonts.splitlines()[2:]):
            raise ValueError('PDF字体未完整嵌入')
        (output/(name+'-fonts.txt')).write_text(fonts)

    for metric in ('age','export'):
        fig,axes=plt.subplots(2,5,figsize=(12.8,5.4),constrained_layout=True)
        for i,scene in enumerate(scenes):
            for j,network in enumerate(networks):
                ax=axes[i,j];members=[r for r in summary if (r['network_id'],r['scenario'])==(network,scene)]
                if not members:
                    ax.text(.5,.5,'No confirmed initialization',ha='center',va='center',transform=ax.transAxes,fontsize=8)
                    ax.set_xticks([]);ax.set_yticks([])
                else:
                    for k,method in enumerate(METHODS):
                        cases=sorted([r for r in members if r['method']==method],key=lambda r:(r['business_seed'],r['block']))
                        for n,r in enumerate(cases):
                            x=k+(n-3.5)*.07
                            if metric=='age':
                                lo,hi=r['controller_loss'];y=(lo+hi)/2
                                ax.errorbar(x,y,yerr=[[y-lo],[hi-y]],fmt='o',color=COLORS[k],markerfacecolor=COLORS[k] if r['block']==0 else 'white',markersize=2.6,capsize=1,elinewidth=.6)
                            else:
                                value=r['export_bytes_per_s']
                                if value is None:
                                    ax.text(k,1.14,'N/A',ha='center',fontsize=6,color=COLORS[k]);continue
                                y=value/(450*r['flows'])
                                ax.scatter([x],[y],s=9,edgecolor=COLORS[k],facecolor=COLORS[k] if r['block']==0 else 'white')
                            if r['window_feasibility']!='within':
                                ax.scatter([x],[y],marker='x',color='#B02525',s=15,zorder=5)
                    ax.set_xticks(range(6),('S','F','PJ','PG','I','D'));ax.set_xlim(-.5,5.5)
                    ax.grid(alpha=.2)
                    if metric=='export':ax.axhline(1,linestyle='--',color='#777777',linewidth=.7);ax.set_ylim(0,1.26)
                    else:ax.set_ylim(0,4.1)
                ax.set_title(f'{network[1]} nodes / {network[3]} flows')
                if j==0:ax.set_ylabel(('Stable' if i==0 else 'Priority')+(' controller age loss' if metric=='age' else ' export / budget'))
        fig.text(.5,-.035,'S: static. F: feedback. PJ: phase joint. PG: phase guard. I: INTOPP-TT. D: D-INTO-TT. Filled/hollow: deployment blocks. Red crosses: budget exclusions.',ha='center',fontsize=7.5)
        finish(fig,'scale-comparison-'+metric)
    for metric in ('age','api'):
        fig,axes=plt.subplots(2,2,figsize=(9,5.8),constrained_layout=True)
        for i,scene in enumerate(scenes):
            for j,(axis_networks,xvalues,label) in enumerate(((('n3f2','n5f2','n8f2'),(3,5,8),'Sampling nodes'),(('n5f2','n5f4','n5f8'),(2,4,8),'Carrier flows'))):
                ax=axes[i,j]
                for k,method in enumerate(METHODS):
                    ys,loerr,hierr=[],[],[]
                    for network in axis_networks:
                        cases=[r for r in summary if (r['network_id'],r['scenario'],r['method'])==(network,scene,method)]
                        if not cases:ys.append(math.nan);loerr.append(0);hierr.append(0);continue
                        if metric=='age':
                            lo=sum(r['controller_loss'][0] for r in cases)/8;hi=sum(r['controller_loss'][1] for r in cases)/8
                            ys.append((lo+hi)/2);loerr.append((hi-lo)/2);hierr.append((hi-lo)/2)
                        else:ys.append(sum(r['api_bytes_per_s'] for r in cases)/8);loerr.append(0);hierr.append(0)
                    ax.errorbar(xvalues,ys,yerr=[loerr,hierr],label=LABELS[k],color=COLORS[k],marker=('o','s','D','^','v','P')[k],markersize=4,linewidth=1,capsize=2)
                if metric=='api':ax.axhline(1200,linestyle='--',color='#777777',linewidth=.7)
                ax.set_xticks(xvalues);ax.set_xlabel(label);ax.grid(alpha=.2);ax.set_ylim(bottom=0)
                ax.set_ylabel(('Stable' if i==0 else 'Priority')+(' controller age loss' if metric=='age' else ' API bytes/s'))
                if i==0:ax.text(5 if j==0 else 2,.02,'N/A',transform=ax.get_xaxis_transform(),ha='center',fontsize=8)
        handles,labels=axes[0,0].get_legend_handles_labels();fig.legend(handles,labels,loc='upper center',bbox_to_anchor=(.5,1.07),ncol=6,frameon=False,fontsize=8)
        fig.text(.5,-.035,'Four independent businesses, each with two deployments. Missing stable 5-node / 2-flow cell remains excluded.'+(' Bars: conditional measurement bounds.' if metric=='age' else ' Dashed line: API budget.'),ha='center',fontsize=7.5)
        finish(fig,'scale-axis-'+metric)
    record=dict(status='passed',script_sha256=sha(Path(__file__)),analysis_manifest_sha256=sha(analysis/'manifest.json'),
        font_file=str(font),font_sha256=sha(font),png_dpi=300,source_data_sha256={n:sha(analysis/n) for n in ('summary.json','stage-plan.json')},
        artifact_sha256={p.name:sha(p) for p in output.iterdir() if p.is_file()})
    (output/'manifest.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();plot(a.analysis,a.output)
