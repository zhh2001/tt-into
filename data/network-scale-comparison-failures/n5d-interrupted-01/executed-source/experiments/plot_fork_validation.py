#!/usr/bin/env python3
"""从固定分支测量生成年龄图，并从可编辑 drawio 图源导出拓扑。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, findfont
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def new_diagram(path):
    file = ET.Element('mxfile', {'host': 'app.diagrams.net'})
    diagram = ET.SubElement(file, 'diagram', {'id': 'fork', 'name': 'Fixed paths'})
    model = ET.SubElement(diagram, 'mxGraphModel', {'dx': '780', 'dy': '280', 'grid': '1', 'gridSize': '10',
                                                 'page': '1', 'pageWidth': '780', 'pageHeight': '280'})
    root = ET.SubElement(model, 'root')
    ET.SubElement(root, 'mxCell', {'id': '0'})
    ET.SubElement(root, 'mxCell', {'id': '1', 'parent': '0'})
    nodes = [('source', 'Source\nF1, F2, F3', 15, 100, 105, 70),
             ('a', 'Branch 1\nS1, port 2', 180, 30, 110, 55),
             ('b', 'Branch 2\nS2, port 2', 180, 160, 110, 55),
             ('shared', 'Shared outlet\nS3, port 2', 355, 100, 115, 65),
             ('sink', 'Sink\nS4', 510, 105, 95, 55),
             ('dest', 'Destination', 660, 40, 105, 45),
             ('collector', 'Collector', 660, 175, 105, 45),
             ('f1', 'F1', 135, 40, 30, 20), ('f2', 'F2', 135, 190, 30, 20),
             ('f3', 'F3', 250, 245, 30, 20)]
    for name, label, x, y, width, height in nodes:
        style = 'rounded=1;whiteSpace=wrap;html=0;fillColor=#ffffff;strokeColor=#333333;strokeWidth=1;fontFamily=Times New Roman;fontSize=10;'
        if name.startswith('f'):
            style += 'fillColor=none;strokeColor=none;'
        cell = ET.SubElement(root, 'mxCell', {'id': name, 'value': label, 'style': style, 'vertex': '1', 'parent': '1'})
        ET.SubElement(cell, 'mxGeometry', {'x': str(x), 'y': str(y), 'width': str(width), 'height': str(height), 'as': 'geometry'})
    edges = [('e1', 'source', 'a', (1, 0.15, 0, 0.5), [(145, 110.5), (145, 57.5)]),
             ('e2', 'source', 'b', (1, 0.8, 0, 0.5), [(145, 156), (145, 187.5)]),
             ('e3', 'source', 'shared', (0.5, 1, 0.5, 1), [(67.5, 240), (412.5, 240)]),
             ('e4', 'a', 'shared', (1, 0.5, 0, 0.25), [(325, 57.5), (325, 116.25)]),
             ('e5', 'b', 'shared', (1, 0.5, 0, 0.75), [(325, 187.5), (325, 148.75)]),
             ('e6', 'shared', 'sink', (1, 0.5, 0, 0.5), []),
             ('e7', 'sink', 'dest', (1, 0.3, 0, 0.5), [(630, 121.5), (630, 62.5)]),
             ('e8', 'sink', 'collector', (1, 0.75, 0, 0.5), [(630, 146.25), (630, 197.5)])]
    for name, source, target, anchors, points in edges:
        style = 'edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;endFill=1;strokeColor=#333333;strokeWidth=1;'
        style += ';'.join(f'{k}={v}' for k, v in zip(('exitX', 'exitY', 'entryX', 'entryY'), anchors)) + ';'
        cell = ET.SubElement(root, 'mxCell', {'id': name, 'edge': '1', 'parent': '1', 'source': source, 'target': target, 'style': style})
        geom = ET.SubElement(cell, 'mxGeometry', {'relative': '1', 'as': 'geometry'})
        array = ET.SubElement(geom, 'Array', {'as': 'points'})
        for x, y in points:
            ET.SubElement(array, 'mxPoint', {'x': str(x), 'y': str(y)})
    ET.indent(file)
    ET.ElementTree(file).write(path, encoding='utf-8', xml_declaration=True)


def render_diagram(path, font, save):
    cells = ET.parse(path).findall('.//root/mxCell')
    vertices = {}
    fig, axis = plt.subplots(figsize=(7.2, 2.65))
    axis.set_xlim(0, 780)
    axis.set_ylim(280, 0)
    axis.axis('off')
    for cell in cells:
        if cell.get('vertex') != '1':
            continue
        geom = cell.find('mxGeometry')
        box = [float(geom.get(k, '0')) for k in ('x', 'y', 'width', 'height')]
        vertices[cell.get('id')] = box
        x, y, width, height = box
        if 'strokeColor=none' not in cell.get('style', ''):
            axis.add_patch(FancyBboxPatch((x, y), width, height, boxstyle='round,pad=0,rounding_size=4',
                                         facecolor='white', edgecolor='#333333', linewidth=0.9))
        axis.text(x + width / 2, y + height / 2, cell.get('value'), ha='center', va='center', fontproperties=font)
    for cell in cells:
        if cell.get('edge') != '1':
            continue
        style = dict(part.split('=', 1) for part in cell.get('style').split(';') if '=' in part)
        def anchor(name, xkey, ykey):
            x, y, width, height = vertices[name]
            return x + float(style[xkey]) * width, y + float(style[ykey]) * height
        points = [anchor(cell.get('source'), 'exitX', 'exitY')]
        points += [(float(p.get('x')), float(p.get('y'))) for p in cell.findall('.//mxPoint')]
        points += [anchor(cell.get('target'), 'entryX', 'entryY')]
        axis.plot([p[0] for p in points[:-1]], [p[1] for p in points[:-1]], color='#333333', linewidth=0.9)
        axis.add_patch(FancyArrowPatch(points[-2], points[-1], arrowstyle='-|>', mutation_scale=8, linewidth=0.9, color='#333333'))
    save(fig, 'fork-topology')


def age_series(directory, origin, start, end):
    sends = {r['packet_id']: r['send_before_monotonic_ns'] for r in rows(directory / 'send.jsonl')}
    samples = sorted(((sends[r['packet_id']] - origin) / 1000, (r['receive_monotonic_ns'] - origin) / 1000)
                     for r in rows(directory / 'collector.jsonl') if any(item['switch_id'] == 3 for item in r.get('records', [])))
    available = [(g, a) for g, a in samples if a <= start]
    if not available:
        raise RuntimeError('共同出口没有真实预热样本')
    low_generation, high_generation = max(g for g, _ in available), max(a for _, a in available)
    x, upper, lower = [(start - start) / 1e6], [(start - low_generation) / 1000], [(start - high_generation) / 1000]
    for generated, arrived in sorted(samples, key=lambda p: p[1]):
        if not start < arrived < end:
            continue
        x.append((arrived - start) / 1e6)
        upper.append((arrived - low_generation) / 1000)
        lower.append((arrived - high_generation) / 1000)
        low_generation, high_generation = max(low_generation, generated), max(high_generation, arrived)
        x.append((arrived - start) / 1e6)
        upper.append((arrived - low_generation) / 1000)
        lower.append((arrived - high_generation) / 1000)
    x.append((end - start) / 1e6)
    upper.append((end - low_generation) / 1000)
    lower.append((end - high_generation) / 1000)
    return x, upper, lower


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--diagnostic', type=Path, required=True)
    p.add_argument('--ordinary', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--drawio', type=Path, help='可用用户调整后的同格式拓扑重新导出')
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    font_path = Path('/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf')
    assert Path(findfont(FontProperties(fname=str(font_path)))).resolve() == font_path.resolve()
    font = FontProperties(fname=str(font_path), size=9)
    small = FontProperties(fname=str(font_path), size=8)
    plt.rcParams.update({'pdf.fonttype': 42, 'font.family': 'Times New Roman'})
    def save(fig, name):
        fig.tight_layout(pad=0.7)
        fig.savefig(a.output / (name + '.png'), dpi=300, metadata={'Software': 'Matplotlib'})
        fig.savefig(a.output / (name + '.pdf'), metadata={'Author': '', 'Creator': '', 'Title': name})
        plt.close(fig)
    diagram = a.output / 'fork-topology.drawio'
    if a.drawio:
        shutil.copyfile(a.drawio, diagram)
    else:
        new_diagram(diagram)
    render_diagram(diagram, font, save)
    inputs = {}
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.6), sharey=True)
    for axis, directory, name, title in zip(axes, [a.diagnostic, a.ordinary, a.ordinary],
                                           ['tt_fixed', 'scheduled_tt', 'static_capacity2'],
                                           ['Fixed TT-INT (K=1)', 'Scheduled TT-INT (K=1)', 'Fixed TT-INT (K=2)']):
        summary_path = directory / 'summary.json'
        summary = json.loads(summary_path.read_text())
        inputs[str(summary_path)] = sha(summary_path)
        case = next(c for c in summary['cases'] if c['name'] == name)
        for filename in ['send.jsonl', 'collector.jsonl']:
            inputs[str(directory / name / filename)] = sha(directory / name / filename)
        start, change = summary['requirements_window_offset_us'][0]
        end = summary['requirements_window_offset_us'][1][1]
        xs, upper, lower = age_series(directory / name, case['origin_monotonic_ns'], start, end)
        axis.fill_between(xs, lower, upper, color='#447293', alpha=0.2, linewidth=0)
        axis.plot(xs, upper, color='#447293', linewidth=1, label='Shared outlet')
        axis.plot([0, (change - start) / 1e6, (change - start) / 1e6, (end - start) / 1e6],
                  [summary['deadlines_us'][0][2] / 1000] * 2 + [summary['deadlines_us'][1][2] / 1000] * 2,
                  color='#995749', linestyle='--', linewidth=1, label='Deadline')
        axis.axvline((change - start) / 1e6, color='#777777', linestyle=':', linewidth=0.7)
        axis.set_title(title, fontproperties=small)
        axis.set_xlabel('Time in measurement window (s)', fontproperties=small)
        axis.set_xlim(0, 2.4)
        axis.set_ylim(0, 280)
        axis.set_xticks([0, 0.8, 1.6, 2.4])
        for t in axis.get_xticklabels() + axis.get_yticklabels():
            t.set_fontproperties(small)
        axis.spines[['top', 'right']].set_visible(False)
    axes[0].set_ylabel('Age upper bound (ms)', fontproperties=font)
    axes[0].legend(prop=small, frameon=False, loc='lower left')
    save(fig, 'shared-outlet-causal-age')
    manifest = {'sources': inputs, 'script_sha256': sha(Path(__file__)), 'drawio_sha256': sha(diagram),
                'font_file': str(font_path), 'font_sha256': sha(font_path), 'png_dpi': 300,
                'scope': '单次确定性运行，共同出口年龄的因果上界与区间；三对象履约结果另见摘要',
                'comparison': '两个 K=1 配置及 K=2 容量放宽，非相同容量下的性能收益',
                'diagram': '按真实固定路径绘制，源和合流均可首次封装，无新算法架构',
                'artifact_sha256': {path.name: sha(path) for path in a.output.iterdir() if path.is_file()}}
    (a.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
