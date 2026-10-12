"""绘制有限预算与共同观察周期响应，同源TNR PNG及嵌入字体PDF。"""
import argparse
import json
from pathlib import Path
import subprocess
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from scalable_contract import sha


def plot(analysis,output):
    summary=json.loads((analysis/'summary.json').read_text())
    plan=json.loads((analysis/'stage-plan.json').read_text())
    if plan['mode']!='comparison' or len(summary)!=80:
        raise ValueError('图源必须包含全部80例正式敏感性')
    font=Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(font))
    if Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'),fallback_to_default=False)).resolve()!=font.resolve():
        raise ValueError('字体未解析到真正Times New Roman')
    plt.rcParams.update({'font.family':'Times New Roman','font.size':10,'pdf.fonttype':42})
    output.mkdir(parents=True,exist_ok=False)
    colors={'static':'#4B6381','phase_guard':'#B05C3B'}
    labels={'static':'Static TT-INT','phase_guard':'TT-INTO (phase guard)'}

    def finish(fig,name):
        fig.savefig(output/(name+'.png'),dpi=300,bbox_inches='tight')
        fig.savefig(output/(name+'.pdf'),bbox_inches='tight',metadata={'Creator':'Matplotlib','Title':name})
        plt.close(fig)
        fonts=subprocess.check_output(['pdffonts',str(output/(name+'.pdf'))],text=True)
        if not fonts.splitlines()[2:] or any('TimesNewRomanPSMT' not in line or 'yes yes yes' not in line for line in fonts.splitlines()[2:]):
            raise ValueError('PDF字体未完整嵌入')
        (output/(name+'-fonts.txt')).write_text(fonts)

    for name,conditions,xvalues,xlabel in (
        ('sensitivity-budget',('B085','B100','B115'),(.85,1.,1.15),'In-band and export budget multiplier'),
        ('sensitivity-observation',('B100','O250','O1000'),(100,250,1000),'Common report observation period (ms)')):
        fig,axes=plt.subplots(1,2,figsize=(8.2,3.1),constrained_layout=True)
        for ax,observer,title in zip(axes,('collector','controller'),('Collector age loss','Controller age loss')):
            for method in ('static','phase_guard'):
                ys,errors=[],[]
                for condition in conditions:
                    members=[r for r in summary if (r['condition_id'],r['method'])==(condition,method)]
                    lo=sum(r[observer+'_loss'][0] for r in members)/8;hi=sum(r[observer+'_loss'][1] for r in members)/8
                    ys.append((lo+hi)/2);errors.append((hi-lo)/2)
                ax.errorbar(xvalues,ys,yerr=errors,color=colors[method],label=labels[method],marker='o' if method=='static' else 's',capsize=3,linewidth=1.2)
                for x,condition,y in zip(xvalues,conditions,ys):
                    excluded=sum(r['window_feasibility']!='within' for r in summary if (r['condition_id'],r['method'])==(condition,method))
                    if excluded:ax.annotate(f'{excluded}/8 over budget',(x,y),xytext=(0,10),textcoords='offset points',ha='center',fontsize=7,color=colors[method])
            ax.set_xlabel(xlabel);ax.set_ylabel(title);ax.set_xticks(xvalues);ax.set_ylim(bottom=0);ax.grid(alpha=.2)
        axes[0].legend(frameon=False,fontsize=8)
        fig.text(.5,-.045,'Four independent businesses, each with two deployments. Bars show conditional measurement bounds.',ha='center',fontsize=8)
        finish(fig,name)
    fig,axes=plt.subplots(1,3,figsize=(10.2,3.1),constrained_layout=True)
    conditions=('B085','B100','B115','O250','O1000')
    verifications=json.loads((analysis/'verification.json').read_text())['cases']
    actual={(r['batch_id'],r['case']):r for r in verifications}
    for method in ('static','phase_guard'):
        members_by_condition=[[r for r in summary if (r['condition_id'],r['method'])==(condition,method)] for condition in conditions]
        export=[sum(r['export_bytes_per_s'] for r in members)/8 if all(r['export_bytes_per_s'] is not None for r in members) else float('nan') for members in members_by_condition]
        api=[sum(r['api_bytes_per_s'] for r in members)/8 for members in members_by_condition]
        completed=[sum(actual[r['batch_id'],r['case']]['completed_observations'] for r in members)/sum(actual[r['batch_id'],r['case']]['observation_slots'] for r in members) for members in members_by_condition]
        for ax,ys in zip(axes,(export,api,completed)):
            ax.plot(range(5),ys,color=colors[method],label=labels[method],marker='o' if method=='static' else 's',linewidth=1.2)
    axes[0].plot(range(5),(1530,1800,2070,1800,1800),color='#666666',linestyle='--',linewidth=.8,label='Export budget')
    axes[1].axhline(1200,color='#666666',linestyle='--',linewidth=.8)
    for ax,label in zip(axes,('Export bytes/s','Serialized API bytes/s','Completed report slots / planned')):
        ax.set_xticks(range(5),conditions);ax.set_ylabel(label);ax.set_ylim(bottom=0);ax.grid(alpha=.2)
    axes[2].set_ylim(0,1.05);axes[0].legend(frameon=False,fontsize=8)
    finish(fig,'sensitivity-costs')
    record=dict(status='passed',script_sha256=sha(Path(__file__)),analysis_manifest_sha256=sha(analysis/'manifest.json'),
        font_file=str(font),font_sha256=sha(font),png_dpi=300,
        source_data_sha256={n:sha(analysis/n) for n in ('summary.json','stage-plan.json','verification.json')},
        artifact_sha256={p.name:sha(p) for p in output.iterdir() if p.is_file()})
    (output/'manifest.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();plot(a.analysis,a.output)
