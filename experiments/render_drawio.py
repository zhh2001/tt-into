"""将未压缩drawio图源导出为同源Times New Roman PNG和嵌入字体PDF。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def style(cell):
    return dict(part.split('=', 1) for part in cell.get('style', '').split(';') if '=' in part)


def render(source, output):
    tree = ET.parse(source)
    model = tree.find('./diagram/mxGraphModel')
    if model is None:
        raise ValueError('需要未压缩的单页drawio XML')
    width, height = float(model.get('pageWidth')), float(model.get('pageHeight'))
    cells = {c.get('id'): c for c in model.findall('./root/mxCell')}
    font = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    font_manager.fontManager.addfont(str(font))
    resolved = Path(font_manager.findfont(font_manager.FontProperties(family='Times New Roman'), fallback_to_default=False))
    if resolved.resolve() != font.resolve():
        raise ValueError('字体未解析到真正Times New Roman')
    plt.rcParams.update({'font.family': 'Times New Roman', 'pdf.fonttype': 42})
    fig, ax = plt.subplots(figsize=(width/100, height/100))
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    ax.set_xlim(0, width); ax.set_ylim(height, 0); ax.set_aspect('equal'); ax.axis('off')
    geometry = {}
    for ident, cell in cells.items():
        g = cell.find('mxGeometry')
        if cell.get('vertex') == '1':
            if g is None or cell.get('parent') != '1':
                raise ValueError('仅支持顶层可编辑形状 ' + ident)
            geometry[ident] = tuple(float(g.get(k, '0')) for k in ('x', 'y', 'width', 'height'))
    for ident, (x, y, w, h) in geometry.items():
        cell, cfg = cells[ident], style(cells[ident])
        fill = cfg.get('fillColor', '#ffffff')
        stroke = cfg.get('strokeColor', '#24374b')
        kwargs = dict(facecolor='none' if fill == 'none' else fill,
                      edgecolor='none' if stroke == 'none' else stroke,
                      linewidth=float(cfg.get('strokeWidth', '1.2'))*.72,
                      linestyle='--' if cfg.get('dashed') == '1' else '-', zorder=1)
        shape = cfg.get('shape', 'rectangle')
        if shape == 'rhombus':
            patch = Polygon([(x+w/2, y), (x+w, y+h/2), (x+w/2, y+h), (x, y+h/2)], **kwargs)
        elif cfg.get('rounded') == '1':
            patch = FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0,rounding_size=8',**kwargs)
        else:
            patch = Rectangle((x,y),w,h,**kwargs)
        ax.add_patch(patch)
    for ident, cell in cells.items():
        if cell.get('edge') != '1':
            continue
        cfg, g = style(cell), cell.find('mxGeometry')
        def endpoint(which):
            key = cell.get('source' if which == 'exit' else 'target')
            if key not in geometry:
                raise ValueError('连接端点缺失 ' + ident)
            x,y,w,h = geometry[key]
            return x+w*float(cfg.get(which+'X', '.5')), y+h*float(cfg.get(which+'Y', '.5'))
        points = [endpoint('exit')]
        points += [(float(p.get('x')), float(p.get('y'))) for p in g.findall('./Array/mxPoint')]
        points.append(endpoint('entry'))
        color, linewidth = cfg.get('strokeColor', '#24374b'), float(cfg.get('strokeWidth', '1.5'))*.72
        line = '--' if cfg.get('dashed') == '1' else '-'
        start = cfg.get('startArrow', 'none') != 'none'
        end = cfg.get('endArrow', 'block') != 'none'
        for index, (a,b) in enumerate(zip(points,points[1:])):
            if a == b:
                continue
            arrows = '<|-|>' if index==0 and index==len(points)-2 and start and end else \
                     '<|-' if index==0 and start else '-|>' if index==len(points)-2 and end else '-'
            ax.add_patch(FancyArrowPatch(a,b,arrowstyle=arrows,mutation_scale=10,
                color=color,linewidth=linewidth,linestyle=line,shrinkA=0,shrinkB=0,zorder=2))
    for ident, (x,y,w,h) in geometry.items():
        cell, cfg = cells[ident], style(cells[ident])
        if cell.get('value'):
            ax.text(x+w/2,y+h/2,cell.get('value'),ha='center',va='center',
                fontsize=float(cfg.get('fontSize','18'))*.72,color=cfg.get('fontColor','#172637'),
                linespacing=1.15,zorder=3)
    output.mkdir(parents=True,exist_ok=True)
    base = output / source.stem
    for suffix in ('.png','.pdf'):
        target = base.with_suffix(suffix)
        if target.exists():
            raise FileExistsError(target)
        fig.savefig(target,dpi=300,metadata={'Creator':'Matplotlib','Title':source.stem} if suffix=='.pdf' else None)
    plt.close(fig)
    fonts=subprocess.check_output(['pdffonts',str(base.with_suffix('.pdf'))],text=True)
    lines=fonts.splitlines()[2:]
    if not lines or any('TimesNewRomanPSMT' not in line or 'yes yes yes' not in line for line in lines):
        raise ValueError('PDF字体未完整嵌入')
    base.with_name(base.name+'-fonts.txt').write_text(fonts)
    return dict(source_file=source.name,source_sha256=sha(source),script_sha256=sha(Path(__file__)),
        font_file=str(font),font_sha256=sha(font),png_dpi=300,
        artifact_sha256={p.name:sha(p) for p in (base.with_suffix('.png'),base.with_suffix('.pdf'),base.with_name(base.name+'-fonts.txt'))})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();record=render(args.source,args.output)
    (args.output/(args.source.stem+'-manifest.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(status='passed',source=args.source.name,artifacts=list(record['artifact_sha256'])),ensure_ascii=False))
