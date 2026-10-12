"""有限规模静态训练的全部预算点及控制器目标，同源TNR图。"""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from scalable_contract import sha


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();scores=json.loads((a.analysis/'candidate-scores.json').read_text());chosen=json.loads((a.analysis/'selected-candidates.json').read_text())
    font=Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf');font_manager.fontManager.addfont(str(font))
    resolved=Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'),fallback_to_default=False))
    if resolved.resolve()!=font.resolve():raise ValueError('未解析到真实Times New Roman')
    plt.rcParams.update({'font.family':'Times New Roman','font.size':9,'pdf.fonttype':42,'ps.fonttype':42})
    a.output.mkdir(parents=True,exist_ok=False)
    fig,axes=plt.subplots(2,5,figsize=(12.6,5),constrained_layout=True)
    names=('n3f2','n5f2','n8f2','n5f4','n5f8')
    for i,scene in enumerate(('stable','shared_priority')):
        for j,name in enumerate(names):
            ax=axes[i,j];pool=[r for r in scores if r['network_id']==name and r['scenario']==scene]
            for ok,marker,color,label in [(True,'o','#315C8C','Within both'),(False,'x','#BA6A35','Budget failure')]:
                rs=[r for r in pool if r['eligible']==ok]
                ax.scatter([r['mean_export_bytes_per_s'] for r in rs],[r['mean_controller_upper'] for r in rs],s=17,marker=marker,color=color,label=label)
            selected=next(r for r in chosen if r['network_id']==name and r['scenario']==scene)['selected']
            if selected:
                r=selected[0];ax.scatter([r['mean_export_bytes_per_s']],[r['mean_controller_upper']],s=65,marker='*',color='#21774B',label='First candidate')
                ax.annotate(r['candidate_id'],(r['mean_export_bytes_per_s'],r['mean_controller_upper']),xytext=(4,4),textcoords='offset points',fontsize=8)
            ax.set_title(f'{name[1]} nodes / {name[3]} flows');ax.grid(alpha=.2)
            if i==1:ax.set_xlabel('Mean export attempts (B/s)')
            if j==0:ax.set_ylabel(('Stable' if i==0 else 'Priority')+' controller age loss')
    handles,labels=axes[0,0].get_legend_handles_labels();fig.legend(handles,labels,loc='lower center',bbox_to_anchor=(.5,-.065),ncol=3,frameon=False)
    fig.savefig(a.output/'n5b-static-candidates.png',dpi=300,bbox_inches='tight')
    fig.savefig(a.output/'n5b-static-candidates.pdf',bbox_inches='tight',metadata={'Creator':'Matplotlib','Title':'Finite static training across network sizes'})
    plt.close(fig)
    record=dict(scope='双训练业务有限候选，不是独立测试排名',script_sha256=sha(Path(__file__)),
        data_sha256={n:sha(a.analysis/n) for n in ('candidate-scores.json','selected-candidates.json')},
        font=str(resolved),png_dpi=300,artifact_sha256={p.name:sha(p) for p in a.output.iterdir() if p.is_file()})
    (a.output/'manifest.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
