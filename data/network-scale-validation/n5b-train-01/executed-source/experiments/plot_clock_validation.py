#!/usr/bin/env python3
"""从时钟检查和普通版 RPC 账本导出 Times New Roman 图片。"""
import argparse
import hashlib
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties,findfont


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--clock',type=Path,required=True)
    p.add_argument('--contract',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    fp=Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    assert Path(findfont(FontProperties(fname=str(fp)))).resolve()==fp.resolve()
    font=FontProperties(fname=str(fp),size=10)
    plt.rcParams.update({'pdf.fonttype':42,'font.family':'Times New Roman'})
    sources={}
    def load(path):
        sources[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
        return json.loads(path.read_text())
    def style(axis):
        for t in axis.get_xticklabels()+axis.get_yticklabels():t.set_fontproperties(font)
        axis.spines[['top','right']].set_visible(False)
    def save(fig,name):
        fig.tight_layout()
        fig.savefig(a.output/(name+'.png'),dpi=300,metadata={'Software':'Matplotlib'})
        fig.savefig(a.output/(name+'.pdf'),metadata={'Author':'','Creator':'','Title':name})
        plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(7.2,3.0))
    for axis,name,title,color in zip(axes,['legacy-clock-jump-control-01','steady-clock-jump-01'],
                                    ['Original elapsed clock','Steady elapsed clock'],['#9b4d3b','#446b8b']):
        rows=load(a.clock/name/'clock-series.json')
        scale=1000 if name.startswith('steady') else 1
        axis.plot([r['send_elapsed_s'] for r in rows],
                  [r['source_elapsed_minus_send_elapsed_s']*scale for r in rows],
                  marker='o',markersize=3,linewidth=1.2,color=color)
        axis.set_title(title,fontproperties=font)
        axis.set_xlabel('Time after first send (s)',fontproperties=font)
        axis.set_ylabel('Elapsed difference ('+('ms' if scale==1000 else 's')+')',fontproperties=font)
        style(axis)
    save(fig,'in-process-clock-jump')
    summary=load(a.contract/'summary.json')
    row=next(r for r in summary['runs'] if r['run']=='steady-task-ordinary-01')
    groups=['installation','observation','update','calibration']
    labels=['Install / reset','Observation','Update','Loss injection']
    request=[row['metered_rpc_by_kind'][k]['request_bytes']/1000 for k in groups]
    reply=[row['metered_rpc_by_kind'][k]['reply_bytes']/1000 for k in groups]
    fig,axis=plt.subplots(figsize=(7.2,3.0))
    axis.bar(range(4),request,label='Request',color='#446b8b')
    axis.bar(range(4),reply,bottom=request,label='Reply',color='#c8bca7')
    axis.set_xticks(range(4),labels)
    axis.set_ylabel('Serialized RPC payload (kB)',fontproperties=font)
    axis.set_title('Metered calls across ten validation cases',fontproperties=font)
    axis.legend(prop=font,frameon=False)
    for i,v in enumerate([x+y for x,y in zip(request,reply)]):
        axis.text(i,v+1,f'{v:.3f}',ha='center',fontproperties=font)
    axis.set_ylim(0,max(x+y for x,y in zip(request,reply))*1.2)
    style(axis);save(fig,'metered-rpc-cost')
    manifest={'sources':sources,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'clock_interpretation':'ingress 经过时间减发送经过时间，基于首包，不是年龄或同步误差；两面板单位不同',
              'rpc_interpretation':'持久 Client 的序列化载荷，不含未计量 CLI 与 TCP 开销，不和 Ethernet 帧直接相加'}
    (a.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__': main()
