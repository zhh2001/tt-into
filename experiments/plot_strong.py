"""从实测摘要绘制字段成本、抽样分布和反馈轨迹。"""
import argparse
import hashlib
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams.update({'pdf.fonttype':42,'ps.fonttype':42})
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties,findfont


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs',nargs=4,type=Path,required=True,metavar=('COMMON','COMPACT','TASK','PREFIX'))
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    font_path=Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    if not font_path.exists() or Path(findfont(FontProperties(fname=str(font_path)))).resolve()!=font_path:
        raise RuntimeError('Times New Roman 字体未解析')
    font=FontProperties(fname=str(font_path),size=10)
    sources=[r/'summary.json' for r in a.runs]
    summaries=[json.loads(r.read_text()) for r in sources]
    assert [s['format_name'] for s in summaries]==['common','compact','task','prefix']
    cases=[{c['name']:c for c in s['cases']} for s in summaries]
    common=cases[0]
    counts=[c['shared']['reported_samples_per_node'] for c in cases]
    if any(c!=counts[0] for c in counts): raise RuntimeError('格式成本图需要相同的实测记录分配')
    # 原包和实际选中节点均一致，只用明确场景的成本作字段对照。
    a.output.mkdir(parents=True,exist_ok=False)
    def style(axis):
        for label in axis.get_xticklabels()+axis.get_yticklabels(): label.set_fontproperties(font)
        axis.spines[['top','right']].set_visible(False)
    def save(fig,name):
        fig.tight_layout()
        fig.savefig(a.output/(name+'.png'),dpi=300,metadata={'Software':'Matplotlib'})
        fig.savefig(a.output/(name+'.pdf'),metadata={'Title':name,'Author':'','Creator':''})
        plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(7.2,3.0))
    costs=[c['shared']['measured_cost']['total_byte_hops'] for c in cases]
    padded=[c['shared']['measured_cost']['total_byte_hops_with_minimum_ethernet_padding'] for c in cases]
    axes[0].bar([i-.18 for i in range(4)],costs,width=.34,color='#446b8b',edgecolor='black',linewidth=.3,label='Captured')
    axes[0].bar([i+.18 for i in range(4)],padded,width=.34,color='#9a8053',hatch='//',edgecolor='black',linewidth=.3,label='With min. padding')
    axes[0].set_xticks(range(4),['Common','Compact','Task','Prefix'])
    axes[0].set_ylabel('Telemetry cost (byte-hop)',fontproperties=font)
    axes[0].set_title('Same observed record allocation',fontproperties=font)
    axes[0].legend(prop=font,loc='upper right')
    for i,policy in enumerate(['uniform','weighted']):
        counts=common[policy]['reported_samples_per_node']
        frequencies=[v/sum(counts) for v in counts]
        positions=[n+(i-.5)*.28 for n in range(3)]
        axes[1].bar(positions,frequencies,width=.26,color=['#446b8b','#9a8053'][i],hatch=['','//'][i],
                    edgecolor='black',linewidth=.3,label=policy.capitalize())
        expected=[1/3]*3 if policy=='uniform' else [1/2,1/3,1/6]
        axes[1].plot(positions,expected,'k_',markersize=10)
    axes[1].set_xticks(range(3),['Node 1','Node 2','Node 3'])
    axes[1].set_ylim(0,.6)
    axes[1].set_ylabel('Fraction of received samples',fontproperties=font)
    axes[1].set_title('One run, 768 packets per policy',fontproperties=font)
    axes[1].plot([],[],'k_',markersize=10,label='Expected')
    axes[1].legend(prop=font,loc='upper right')
    for axis in axes: style(axis)
    save(fig,'strong-format-and-reservoir')
    case=common['feedback_pro']
    origin=case['start_monotonic_ns']
    events=[row for row in case['controller'] if row['node']==1]
    times=[0]+[(r['after_monotonic_ns']-origin)/1e6 for r in events]+[(case['end_monotonic_ns']-origin)/1e6]
    states=[0]+[r['value'] for r in events]+[events[-1]['value']]
    packets=json.loads((a.runs[0]/'feedback_pro/packets.json').read_text())
    fig,axis=plt.subplots(figsize=(7.2,3.0))
    axis.axvspan(0,400,color='#dedede',label='Configured export loss interval')
    axis.step(times,states,where='post',color='#446b8b',linewidth=1.5,label='Node 1 force flag')
    axis.axvline(max(r['offset_ns'] for r in packets)/1e6,color='black',linestyle=':',label='Last scheduled business packet')
    axis.set_yticks([0,1])
    axis.set_ylim(-.1,1.15)
    axis.set_xlabel('Time after scheduled start (ms)',fontproperties=font)
    axis.set_ylabel('Force flag',fontproperties=font)
    axis.set_title('Boolean feedback reacts to export loss and later idle time',fontproperties=font,pad=30)
    axis.legend(prop=FontProperties(fname=str(font_path),size=8.5),loc='lower left',bbox_to_anchor=(0,1.0),ncol=3)
    style(axis)
    save(fig,'strong-feedback-timeline')
    manifest={'sources':{str(r):hashlib.sha256(r.read_bytes()).hexdigest() for r in sources},
              'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'interpretation':'实测单次构造检查，概率标记为流式蓄水池理论边际，不是置信区间或性能排名'}
    # 公开源路径使用相对于数据根的目录名。
    manifest['sources']={s['run_id']:hashlib.sha256(r.read_bytes()).hexdigest() for s,r in zip(summaries,sources)}
    (a.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__': main()
