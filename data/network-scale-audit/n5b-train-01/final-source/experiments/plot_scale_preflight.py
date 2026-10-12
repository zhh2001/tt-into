"""由N5b实测汇总生成正确性与观察入口诊断图。"""
import argparse
import json
from pathlib import Path
import hashlib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    font=Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({'font.family':'Times New Roman','font.size':10,'pdf.fonttype':42,'ps.fonttype':42})
    resolved=Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'),fallback_to_default=False))
    if resolved.resolve()!=font.resolve():raise ValueError('字体未解析到真实Times New Roman')
    data=json.loads((a.analysis/'summary.json').read_text())
    fig,axes=plt.subplots(1,2,figsize=(10,3.3),constrained_layout=True)
    labels=['Static','Feedback','Phase joint','Phase guard','INTOPP-TT','D-INTO-TT']
    axes[0].bar(range(6),[r['paid_counts'] for r in data],color='#315C8C')
    axes[1].bar(range(6),[r['observer_max_delay_ms'] for r in data],color='#BA6A35')
    for ax in axes:
        ax.set_xticks(range(6),labels,rotation=25,ha='right');ax.grid(axis='y',alpha=.25);ax.set_xlabel('Correctness case')
    axes[0].set_ylabel('Paid source-count reads')
    axes[1].set_ylabel('Maximum report-view delay (ms)')
    fig.savefig(a.output/'n5b-entry-diagnostics.png',dpi=300)
    fig.savefig(a.output/'n5b-entry-diagnostics.pdf',metadata={'Creator':'Matplotlib','Title':'Fixed-network entry correctness diagnostics'})
    plt.close(fig)
    manifest=dict(scope='确定性入口诊断，非性能比较',data_sha256=hashlib.sha256((a.analysis/'summary.json').read_bytes()).hexdigest(),font=str(resolved),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),png_dpi=300,
        artifacts={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in a.output.iterdir() if p.is_file()})
    (a.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
