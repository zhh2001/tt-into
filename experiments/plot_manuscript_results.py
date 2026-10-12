"""由冻结的公开结果生成论文栏宽下的汇总图，不改变实验或统计口径。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
METHODS = ('static', 'feedback', 'local', 'joint', 'phase_joint',
           'uniform_guard', 'phase_guard', 'phase_local_guard', 'intopp_due', 'd_into_tt')
LABELS = ('S', 'F', 'L', 'J', 'PJ', 'UG', 'PG', 'PL', 'I', 'D')
COLORS = ('#444444', '#888888', '#b39565', '#aa668b', '#3977ad',
          '#859551', '#b24630', '#70639a', '#337d70', '#c19125')
SCALE_METHODS = ('static', 'feedback', 'phase_joint', 'phase_guard', 'intopp_due', 'd_into_tt')
MARKERS = ('o', 's', '^', 'D', 'v', 'P')
MAIN = ROOT / 'data/literature-comparison-validation/n4g-comparison-03'
SCALE = ROOT / 'data/network-scale-comparison-validation/n5d-comparison-02'
SENS = ROOT / 'data/network-scale-sensitivity-validation/n5e-comparison-01'
SOFTWARE = ROOT / 'data/network-scale-comparison-audit/n5d-comparison-02/software-diagnostics.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bounds(rows, lower, upper):
    return np.array([np.mean([float(row[lower]) for row in rows]),
                     np.mean([float(row[upper]) for row in rows])])


def json_bounds(rows, key):
    return np.mean([row[key] for row in rows], axis=0)


def style(ax):
    ax.grid(axis='y', color='#dddddd', linewidth=.5)
    ax.set_axisbelow(True)
    ax.spines[['top', 'right']].set_visible(False)
    ax.tick_params(width=.6, length=3)


def line(ax, x, intervals, label, color, marker, linestyle='-'):
    values = np.array(intervals)
    means = values.mean(axis=1)
    ax.errorbar(x, means, yerr=(values[:, 1]-values[:, 0])/2,
                label=label, color=color, marker=marker, linestyle=linestyle,
                markersize=4, linewidth=1, capsize=2)


def main_age(aggregate, cases):
    fig, axes = plt.subplots(2, 2, figsize=(7.1, 4.5))
    scenes = ('stable', 'shared_priority', 'switch', 'bursty_jitter')
    titles = ('(a) Stable', '(b) Shared priority', '(c) Supply / demand switch', '(d) Jittered bursts')
    for ax, scene, title in zip(axes.flat, scenes, titles):
        rows = {r['method']: r for r in aggregate if r['scenario'] == scene}
        for index, method in enumerate(METHODS):
            pair = [float(rows[method][f'controller_mean_loss_{end}']) for end in ('lower', 'upper')]
            ax.bar(index, np.mean(pair), width=.65, color=COLORS[index], alpha=.65,
                   edgecolor=COLORS[index], linewidth=.6)
            ax.errorbar(index, np.mean(pair), yerr=(pair[1]-pair[0])/2,
                        color='black', capsize=2, linewidth=.7)
            group = sorted((r for r in cases if r['scenario'] == scene and r['method'] == method),
                           key=lambda r: (int(r['seed']), int(r['block'])))
            assert len(group) == 8
            for offset, row in zip(np.linspace(-.22, .22, 8), group):
                value = (float(row['controller_loss_lower'])+float(row['controller_loss_upper']))/2
                failed = row['window_feasibility'] != 'within'
                ax.scatter(index+offset, value, s=13 if failed else 6,
                           color='#c1272d' if failed else '#222222', marker='x' if failed else '.', zorder=4)
        ax.set_xticks(range(10), LABELS)
        ax.set_ylabel('Controller age loss, J')
        ax.set_title(title, fontsize=10)
        style(ax)
    fig.subplots_adjust(left=.09, right=.99, bottom=.09, top=.94, hspace=.38, wspace=.3)
    return fig


def main_costs(aggregate):
    rows = {r['method']: r for r in aggregate if r['scenario'] == 'shared_priority'}
    fig, axes = plt.subplots(1, 2, figsize=(7.1, 2.8))
    ax = axes[0]
    for i, method in enumerate(METHODS):
        r = rows[method]
        loss = np.mean([float(r[f'controller_mean_loss_{k}']) for k in ('lower', 'upper')])
        export = np.mean([float(r[f'export_{k}_bytes_per_s']) for k in ('lower', 'upper')])
        ax.scatter(export, loss, s=23, color=COLORS[i], marker=MARKERS[i % 6])
        # 接近的点保留各自标记，并错开文本。
        offsets = {'static': (-12, -13), 'feedback': (-12, 8), 'local': (6, -12),
                   'joint': (5, 4), 'phase_joint': (-15, 7), 'phase_guard': (5, -10)}
        ax.annotate(LABELS[i], (export, loss), xytext=offsets.get(method, (5, 5)),
                    textcoords='offset points', fontsize=8)
    ax.set_xlabel('Export traffic (B/s)'); ax.set_ylabel('Controller age loss, J')
    ax.set_title('(a) Age and export traffic', fontsize=10); style(ax)
    ax.set_xlim(400, 975)
    ax.set_ylim(.86, 1.55)
    ax = axes[1]
    selected = ('static', 'phase_joint', 'phase_guard', 'intopp_due', 'd_into_tt')
    layers = ('e1', 'e2', 'e3', 'export', 'api', 'local_notice')
    limits = (240, 240, 390, 900, 1200, 128)
    for i, method in enumerate(selected):
        rates = [float(rows[method][f'{r}_upper_bytes_per_s']) if r in ('e1','e2','e3','export')
                 else float(rows[method][f'{r}_bytes_per_s']) for r in layers]
        idx = METHODS.index(method)
        ax.bar(np.arange(6)+(i-2)*.14, np.array(rates)/limits, width=.14,
               color=COLORS[idx], label=LABELS[idx], edgecolor='white', linewidth=.3)
    ax.axhline(1, linestyle='--', color='#555555', linewidth=.8)
    ax.set_xticks(range(6), ('L1','L2','L3','Export','API','Notice'))
    ax.set_ylabel('Consumption / own-layer budget')
    ax.set_title('(b) Separate resource layers', fontsize=10)
    ax.legend(ncol=5, loc='upper center', bbox_to_anchor=(.5, 1.01), fontsize=7.5,
              handlelength=.8, columnspacing=.65, frameon=False)
    ax.set_ylim(0, 1.2); style(ax)
    fig.subplots_adjust(left=.09, right=.99, bottom=.19, top=.86, wspace=.38)
    return fig


def scale_age(rows):
    fig, axes = plt.subplots(2, 2, figsize=(7.1, 4.8))
    for index, (ax, scene, axis) in enumerate(zip(axes.flat,
            ('stable','stable','shared_priority','shared_priority'), ('nodes','flows','nodes','flows'))):
        xs = (3,5,8) if axis == 'nodes' else (2,4,8)
        for method, marker in zip(SCALE_METHODS, MARKERS):
            intervals=[]
            for x in xs:
                group=[r for r in rows if r['scenario']==scene and r['method']==method and
                       r['sampling_nodes']==(x if axis=='nodes' else 5) and
                       r['flows']==(2 if axis=='nodes' else x)]
                assert len(group) in (0,8)
                intervals.append(json_bounds(group,'controller_loss') if group else [np.nan,np.nan])
            idx=METHODS.index(method)
            line(ax,xs,intervals,LABELS[idx],COLORS[idx],marker)
        ax.set_xticks(xs);ax.set_ylabel('Controller age loss, J')
        ax.set_xlabel('Sampling nodes (2 flows)' if axis=='nodes' else 'Carrier flows (5 nodes)')
        ax.set_title(f'({chr(97+index)}) '+('Stable' if scene=='stable' else 'Shared priority'),fontsize=10)
        style(ax)
    handles,labels=axes[0,0].get_legend_handles_labels()
    fig.legend(handles,labels,ncol=6,loc='upper center',bbox_to_anchor=(.5,1),frameon=False)
    fig.subplots_adjust(left=.09,right=.99,bottom=.1,top=.88,hspace=.48,wspace=.3)
    return fig


def software_cost(data):
    rows=data['groups'];fig,axes=plt.subplots(1,2,figsize=(7.1,2.7))
    for ax, axis in zip(axes, ('nodes','flows')):
        xs=(3,5,8) if axis=='nodes' else (2,4,8)
        for method in ('phase_joint','phase_guard'):
            means=[]
            for x in xs:
                name=f'n{x}f2' if axis=='nodes' else f'n5f{x}'
                group=[r for r in rows if r['network_id']==name and r['method']==method]
                means.append(np.average([r['solver_mean_ms'] for r in group],
                                        weights=[r['cases'] for r in group]))
            idx=METHODS.index(method)
            ax.plot(xs,means,label=LABELS[idx],color=COLORS[idx],marker='o',linewidth=1)
        ax.set_xticks(xs);ax.set_ylabel('Mean decision time (ms)')
        ax.set_xlabel('Sampling nodes (2 flows)' if axis=='nodes' else 'Carrier flows (5 nodes)')
        ax.legend(frameon=False);style(ax)
    fig.subplots_adjust(left=.09,right=.99,bottom=.2,top=.95,wspace=.3)
    return fig


def sensitivity(rows):
    fig,axes=plt.subplots(1,2,figsize=(7.1,2.8))
    for ax, ids, xs, title in zip(axes, (('B085','B100','B115'),('B100','O250','O1000')),
            ((.85,1,1.15),(100,250,1000)), ('(a) Data-budget multiplier','(b) Observation period (ms)')):
        for method in ('static','phase_guard'):
            idx=METHODS.index(method)
            for key,linestyle in (('controller_loss','-'),('collector_loss','--')):
                pairs=[]
                for condition in ids:
                    group=[r for r in rows if r['condition_id']==condition and r['method']==method]
                    assert len(group)==8
                    pairs.append(json_bounds(group,key))
                line(ax,xs,pairs,LABELS[idx]+(' controller' if key=='controller_loss' else ' collector'),
                     COLORS[idx],'o' if method=='static' else 'D',linestyle)
        ax.set_xticks(xs);ax.set_ylabel('Age loss, J');ax.set_xlabel(title);style(ax)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,ncol=4,loc='upper center',bbox_to_anchor=(.5,1),fontsize=8,frameon=False)
    fig.subplots_adjust(left=.09,right=.99,bottom=.2,top=.85,wspace=.3)
    return fig


def run(output):
    if output.exists():
        raise FileExistsError('请用新输出目录保留已有图：'+str(output))
    font=Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(font))
    resolved=Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'),fallback_to_default=False))
    if resolved.resolve()!=font.resolve():raise ValueError('Times New Roman解析错误')
    plt.rcParams.update({'font.family':'Times New Roman','font.size':9,'pdf.fonttype':42,
                         'axes.linewidth':.6,'axes.unicode_minus':False})
    sources=(MAIN/'method-aggregate.csv',MAIN/'summary.csv',SCALE/'summary.json',SENS/'summary.json',SOFTWARE)
    aggregate=list(csv.DictReader(sources[0].open()));cases=list(csv.DictReader(sources[1].open()))
    scale=json.loads(sources[2].read_text());sens=json.loads(sources[3].read_text());sw=json.loads(sources[4].read_text())
    assert len(aggregate)==40 and len(cases)==320 and len(scale)==432 and len(sens)==80
    output.mkdir(parents=True)
    figures={'main-controller-age':main_age(aggregate,cases),'main-priority-costs':main_costs(aggregate),
             'scale-controller-age':scale_age(scale),'scale-software-cost':software_cost(sw),
             'sensitivity-response':sensitivity(sens)}
    for name,fig in figures.items():
        fig.savefig(output/(name+'.png'),dpi=300)
        fig.savefig(output/(name+'.pdf'))
        plt.close(fig)
        fonts=subprocess.check_output(['pdffonts',str(output/(name+'.pdf'))],text=True)
        if not fonts.splitlines()[2:] or any('TimesNewRomanPSMT' not in r or 'yes yes yes' not in r
                                            for r in fonts.splitlines()[2:]):
            raise ValueError('字体未完整嵌入 '+name)
        (output/(name+'-fonts.txt')).write_text(fonts)
    manifest={'status':'passed','script_sha256':sha(Path(__file__)),
              'sources':{str(p.relative_to(ROOT)):sha(p) for p in sources},
              'source_rows':{'main':320,'scale':432,'sensitivity':80},
              'font_sha256':sha(font),'png_dpi':300,
              'intervals':'同业务测量条件界的算术均值，不是置信区间。主图小点保留八个部署，红叉保留预算非within。',
              'scale_gap':'n5f2 stable无确认配置，保留空缺。软件图按实际案例数加权各单元平均决策耗时。',
              'artifacts':{p.name:sha(p) for p in sorted(output.iterdir())}}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':'passed','figures':len(figures),'source_rows':manifest['source_rows']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
