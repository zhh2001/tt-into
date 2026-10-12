"""绘制三块静态控制器年龄与共同观察可用性。"""
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
    if audit['status']!='passed' or audit['cases']!=72 or audit['analysis_manifest_sha256']!=sha(analysis/'manifest.json'):
        raise ValueError('绘图需要完整72例独立验收')
    for name in ('summary.csv','shortlist.json','frozen-static.json','report-availability.csv'):
        if sha(analysis/name)!=manifest['artifact_sha256'][name]: raise ValueError('图源改变')
    table=list(csv.DictReader((analysis/'summary.csv').open()))
    groups=load(analysis/'shortlist.json')['groups']; winners=load(analysis/'frozen-static.json')
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
    fig,axes=plt.subplots(2,2,figsize=(8.2,6.1))
    colors=('#286f95','#ad682e')
    for axis,group,label in zip(axes.flat,groups,LABELS):
        winner=next(w for w in winners if w['scenario']==group['scenario'])
        for i,candidate in enumerate(group['candidates']):
            values=sorted([r for r in table if r['scenario']==group['scenario'] and int(r['candidate_index'])==candidate['candidate_index']],key=lambda r:(int(r['seed']),int(r['block'])))
            for j,r in enumerate(values):
                lo,hi=float(r['controller_loss_lower']),float(r['controller_loss_upper'])
                axis.errorbar(i+(j-2.5)*.072,(lo+hi)/2,yerr=[[(hi-lo)/2],[(hi-lo)/2]],fmt='o' if int(r['seed'])==11 else 's',
                    color=colors[j//3],markersize=4,capsize=2,linewidth=.7)
                if r['window_feasibility']!='within': axis.scatter([i+(j-2.5)*.072],[hi],marker='x',color='#a83c30',s=42,zorder=5)
        selected=winner['selected_candidate']
        axis.set_title(label+(f'   Frozen C{selected:02d}' if selected is not None else '   No feasible candidate'))
        axis.set_xticks(range(3),[f"C{c['candidate_index']:02d}\n"+('Previous' if i==0 else 'Training' if i==1 else 'Challenger') for i,c in enumerate(group['candidates'])])
        axis.set_xlim(-.4,2.4); axis.set_ylabel('Controller age-loss conditional bounds')
        axis.grid(axis='y',color='#dddddd',linewidth=.4);axis.set_axisbelow(True)
    fig.legend(handles=[Line2D([],[],color=colors[0],marker='o',linestyle='none',label='Business 11, three blocks'),
        Line2D([],[],color=colors[1],marker='s',linestyle='none',label='Business 29, three blocks')],loc='upper center',ncol=2,frameon=False,fontsize=8)
    fig.text(.5,.018,'Two training businesses, three deployments each. Error bars show conditional age bounds.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.055,1,.94));finish(fig,'controller-static-confirmation','Shared observer static configuration confirmation')
    reports=list(csv.DictReader((analysis/'report-availability.csv').open()))
    fig,axis=plt.subplots(figsize=(6.8,3.7))
    for group,label in zip(groups,LABELS):
        names={r['case'] for r in table if r['scenario']==group['scenario']}
        values=sorted(float(r['receipt_to_entry_upper_ms']) for r in reports if r['case'] in names)
        axis.step(values,[(i+1)/len(values) for i in range(len(values))],where='post',label=label,linewidth=1.1)
    axis.axvline(100,color='#777777',linestyle=':',linewidth=.8)
    axis.set_xlabel('Report receipt to controller availability upper bound (ms)');axis.set_ylabel('Empirical cumulative fraction')
    axis.grid(color='#dddddd',linewidth=.4);axis.set_axisbelow(True);axis.set_ylim(0,1.03)
    axis.legend(frameon=False,fontsize=8)
    fig.text(.5,.018,f'Receipt bounds from {len(reports):,} reports across 72 deployments at a 100 ms observation period.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.065,1,1));finish(fig,'observation-confirmation','Shared observer availability during static confirmation')
    source='experiments/plot_observer_confirmation.py';target=output/'executed-source'/source
    target.parent.mkdir(parents=True);shutil.copyfile(ROOT/source,target)
    write(output/'manifest.json',{'status':'passed','analysis_manifest_sha256':sha(analysis/'manifest.json'),
        'independent_checks_sha256':sha(checks),'font_name':font.get_name(),'font_file':str(fp),'font_sha256':sha(fp),
        'source_sha256':{source:sha(ROOT/source)},'artifact_sha256':{str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('analysis','checks','output'): p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();plot(a.analysis,a.checks,a.output)
