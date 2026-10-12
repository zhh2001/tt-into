"""绘制全局参数校准和选中八方法的控制器年龄。"""
import argparse
import csv
from pathlib import Path
import shutil
import subprocess

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D

from analyze_observer_training import write
from check_matched_network import load, sha

ROOT = Path(__file__).resolve().parents[1]
LABELS = ('Stable / high', 'Priority / high', 'Switch / low', 'Bursty jitter / low')


def plot(analysis, checks, output):
    audit=load(checks); manifest=load(analysis/'manifest.json')
    if audit['status']!='passed' or audit['cases']!=184 or audit['analysis_manifest_sha256']!=sha(analysis/'manifest.json'):
        raise ValueError('绘图需要完整184例独立验收')
    for name in ('summary.csv','selected-cases.json','frozen-parameters.json','parameter-scores.json'):
        if sha(analysis/name)!=manifest['artifact_sha256'][name]: raise ValueError('图源改变')
    table=list(csv.DictReader((analysis/'summary.csv').open()))
    selected=load(analysis/'selected-cases.json');winners=load(analysis/'frozen-parameters.json');scores=load(analysis/'parameter-scores.json')
    fp=Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(fp)); font=font_manager.FontProperties(fname=fp)
    if font.get_name()!='Times New Roman' or Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'),fallback_to_default=False)).resolve()!=fp.resolve():
        raise ValueError('真实TNR字体解析失败')
    plt.rcParams.update({'font.family':'Times New Roman','font.size':9,'pdf.fonttype':42,'axes.unicode_minus':False})
    output.mkdir(parents=True,exist_ok=False)
    def finish(fig,name,title):
        fig.savefig(output/(name+'.png'),dpi=300,metadata={'Software':'Matplotlib'})
        fig.savefig(output/(name+'.pdf'),metadata={'Author':'','Creator':'','Title':title})
        plt.close(fig)
        fonts=subprocess.check_output(['pdffonts',str(output/(name+'.pdf'))],text=True)
        if not fonts.splitlines()[2:] or any('TimesNewRoman' not in r or ' yes ' not in r for r in fonts.splitlines()[2:]):
            raise ValueError('PDF字体未正确嵌入')
        (output/(name+'-fonts.txt')).write_text(fonts)
    methods=('static','feedback','local','joint','phase_joint','uniform_guard','phase_guard','phase_local_guard')
    method_labels=('Static','Feedback','Local','Joint','Phase joint','Uniform guard','Phase guard','Phase local guard')
    profiles=('stable','shared_priority','switch','bursty_jitter')
    colors=('#286f95','#ad682e')
    fig,axes=plt.subplots(2,2,figsize=(9,6.7))
    for axis,scenario,label in zip(axes.flat,profiles,LABELS):
        for i,method in enumerate(methods):
            values=sorted([r for r in selected if (r['scenario'],r['method'])==(scenario,method)],key=lambda r:r['seed'])
            for j,r in enumerate(values):
                lo,hi=r['controller_loss_lower'],r['controller_loss_upper']
                axis.errorbar(i+(j-.5)*.19,(lo+hi)/2,yerr=[[(hi-lo)/2],[(hi-lo)/2]],fmt='o' if j==0 else 's',
                    color=colors[j],markersize=4,capsize=2,linewidth=.7)
                if r['window_feasibility']!='within':axis.scatter([i+(j-.5)*.19],[hi],marker='x',color='#a83c30',s=55,zorder=5)
        axis.set_title(label);axis.set_xticks(range(8),method_labels,rotation=45,ha='right')
        axis.set_xlim(-.5,7.5);axis.set_ylabel('Controller age-loss conditional bounds')
        axis.grid(axis='y',color='#dddddd',linewidth=.4);axis.set_axisbelow(True)
    handles=[Line2D([],[],color=colors[0],marker='o',linestyle='none',label='Calibration business 1301'),
        Line2D([],[],color=colors[1],marker='s',linestyle='none',label='Calibration business 1423'),
        Line2D([],[],color='#a83c30',marker='x',linestyle='none',label='Window budget exceeded')]
    fig.legend(handles=handles,loc='upper center',ncol=3,frameon=False,fontsize=8)
    fig.text(.5,.015,'Selected global settings. All methods share static initialization and a 100 ms report observer.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.05,1,.94));finish(fig,'controller-dynamic-calibration','Controller freshness after global calibration')
    fig,axes=plt.subplots(1,3,figsize=(9,3.65))
    for axis,winner in zip(axes,winners):
        values=sorted([s for s in scores if s['method']==winner['method']],key=lambda s:s['parameter_index'])
        for r in values:
            i=r['parameter_index'];values_for_score=[x for x in table if x['case'] in r['case_names']]
            y=sum(float(x['controller_loss_upper']) for x in values_for_score)/8
            axis.bar(i,y,color='#9bb9c8' if i!=winner['selected_parameter'] else '#286f95',width=.65)
            if r['rank'] is None:axis.bar(i,y,color='#dddddd',width=.65,hatch='//')
            elif r['rank'][0]:axis.scatter([i],[y],color='#a83c30',marker='x',s=45,zorder=5)
            axis.text(i,y+.007,str(r['rank'][0]) if r['rank'] is not None else '?',ha='center',va='bottom',fontsize=8)
        axis.set_title(winner['method'].replace('_',' ').title()+f"  P{winner['selected_parameter']:02d}")
        axis.set_xticks(range(6),[f'P{i:02d}' for i in range(6)]);axis.set_ylabel('Mean controller age-loss upper bound')
        axis.set_ylim(0,max(sum(float(x['controller_loss_upper']) for x in table if x['case'] in r['case_names'])/8 for r in values)*1.17)
        axis.grid(axis='y',color='#dddddd',linewidth=.4);axis.set_axisbelow(True)
    fig.text(.5,.018,'Eight cases per preset. Labels show failures or ? for unavailable cost bounds. Dark bars mark selected presets.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.075,1,1));finish(fig,'global-parameter-calibration','Budget and controller freshness global parameter calibration')
    source='experiments/plot_observer_calibration.py';target=output/'executed-source'/source
    target.parent.mkdir(parents=True);shutil.copyfile(ROOT/source,target)
    write(output/'manifest.json',{'status':'passed','analysis_manifest_sha256':sha(analysis/'manifest.json'),
        'independent_checks_sha256':sha(checks),'font_name':font.get_name(),'font_file':str(fp),'font_sha256':sha(fp),
        'source_sha256':{source:sha(ROOT/source)},'artifact_sha256':{str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('analysis','checks','output'): p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();plot(a.analysis,a.checks,a.output)
