#!/usr/bin/env python3
"""检查原始运行记录，导出可公开的数据及 Times New Roman 验证图。"""
from __future__ import annotations

import argparse
from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, findfont
from scapy.utils import RawPcapReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from collector.codec import decode_frame

FONT = Path("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf")


def wire_checks(run: Path) -> dict:
    frames, maximum, invalid, empty = 0, 0, [], 0
    for capture in sorted(run.glob("node-*/*_out.pcap")):
        # BMv2 没有输出包的端口会留下零字节文件。
        if capture.stat().st_size == 0:
            empty += 1
            continue
        with closing(RawPcapReader(str(capture))) as reader:
            for frame, _ in reader:
                try:
                    decoded = decode_frame(frame)
                except ValueError as exc:
                    invalid.append({"capture": capture.name, "error": str(exc)})
                    continue
                if decoded:
                    frames += 1
                    maximum = max(maximum, decoded["ip_length"])
                    if not decoded["ip_checksum_ok"] or decoded["ip_length"] > 1500:
                        invalid.append({"capture": capture.name, "packet_id": decoded["packet_id"]})
    if invalid:
        raise RuntimeError(f"链路抓包校验失败：{invalid[:5]}")
    return {"checked_ipv4_frames": frames, "maximum_output_ip_length": maximum,
            "empty_output_capture_files": empty,
            "ipv4_checksum_and_mtu_passed": True,
            "byte_convention": "抓包帧字节，不含 FCS、前导码或物理层间隔"}


def plot(summary: dict, directory: Path) -> None:
    cases = {row["name"]: row for row in summary["cases"]}
    required = ["udp_full_path", "sparse_capacity_1", "dense_capacity_1"]
    if not all(name in cases for name in required):
        return
    if not FONT.is_file() or Path(findfont(FontProperties(fname=str(FONT)))).resolve() != FONT:
        raise RuntimeError("真实 Times New Roman 字体不可用")
    plt.rcParams.update({"pdf.fonttype": 42, "ps.fonttype": 42})
    font = FontProperties(fname=str(FONT), size=10)
    figure, axes = plt.subplots(1, 3, figsize=(9.0, 2.8), sharey=False)
    for axis, name in zip(axes, required):
        case = cases[name]
        counts = case["samples_per_node"]
        x = list(range(1, len(counts) + 1))
        axis.bar(x, counts, width=0.6, color="#446b8b", edgecolor="#243d51")
        axis.set_xticks(x)
        axis.set_ylim(0, max(counts) * 1.2 + 0.5)
        axis.set_xlabel("Sampling node", fontproperties=font)
        axis.set_ylabel("Inserted records", fontproperties=font)
        title = {"udp_full_path": "200 ms gaps, K = H",
                 "sparse_capacity_1": "200 ms gaps, K = 1",
                 "dense_capacity_1": "10 ms gaps, K = 1"}[name]
        axis.set_title(title + f"\n{case['input_packets']} input packets", fontproperties=font)
        for label in axis.get_xticklabels() + axis.get_yticklabels():
            label.set_fontproperties(font)
        for xi, count in zip(x, counts):
            axis.text(xi, count + 0.2, str(count), ha="center", fontproperties=font)
        axis.spines[["top", "right"]].set_visible(False)
    figure.tight_layout(pad=0.8, w_pad=1.2)
    figure.savefig(directory / "tt-int-core-validation.png", dpi=300,
                   metadata={"Software": "Matplotlib"})
    figure.savefig(directory / "tt-int-core-validation.pdf",
                   metadata={"Title": "TT-INT core validation", "Author": "", "Creator": ""})
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--figures", type=Path)
    parser.add_argument("--refresh", action="store_true", help="重生成同一运行的已有派生目录")
    args = parser.parse_args()
    run, output = args.run.resolve(), args.output.resolve()
    if output == run or output in run.parents or run in output.parents:
        raise RuntimeError("派生目录与原始运行目录必须分开")
    manifest = json.loads((run / "manifest.json").read_text())
    if manifest["status"] != "passed":
        raise RuntimeError("该运行未通过，不将其导出为通过记录；原始失败数据应保留")
    runtime_sources = {"src/p4/tt_int.p4", "src/collector/codec.py", "experiments/build.py",
                       "experiments/topology.py", "experiments/packet_io.py",
                       "experiments/run_validation.py"}
    if manifest.get("diagnostics_enabled") is False:
        runtime_sources.add("experiments/run_smoke.py")
    for source in sorted(runtime_sources):
        expected = manifest["source_sha256"][source]
        if hashlib.sha256((ROOT / source).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"已执行源文件与当前版本不一致：{source}")
    summary = dict(manifest)
    summary["verified_measurement_sources"] = sorted(runtime_sources)
    summary["wire_checks"] = wire_checks(run)
    summary["analysis_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    summary["interpretation"] = "单次确定性正确性检查；各场景输入包数不同，不作为等预算性能比较"
    if output.exists():
        if not args.refresh:
            raise RuntimeError("派生目录已存在，如需重生成同一运行请加 --refresh")
        prior = json.loads((output / "summary.json").read_text())
        if prior["run_id"] != manifest["run_id"] or prior["source_sha256"] != manifest["source_sha256"]:
            raise RuntimeError("已有派生目录来自不同运行，不能覆盖")
    output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for case in manifest["cases"]:
        src, dst = run / case["name"], output / case["name"]
        dst.mkdir(exist_ok=True)
        for pattern in ["packets-*.json", "send-*.jsonl", "observations.jsonl",
                        "destination.jsonl", "collector.jsonl", "destination.pcap", "collector.pcap"]:
            for original in sorted(src.glob(pattern)):
                target = dst / original.name
                shutil.copyfile(original, target)
                hashes[str(target.relative_to(output))] = hashlib.sha256(target.read_bytes()).hexdigest()
    summary["public_artifact_sha256"] = hashes
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    with open(output / "sample-counts.csv", "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["case", "node_id", "samples", "input_packets", "threshold_us", "capacity"])
        for case in manifest["cases"]:
            for node, count in enumerate(case.get("samples_per_node", []), 1):
                writer.writerow([case["name"], node, count, case["input_packets"],
                                 case["threshold_us"], case["capacity"]])
    if args.figures:
        args.figures.mkdir(parents=True, exist_ok=True)
        plot(summary, args.figures)
    print(f"已导出 {len(manifest['cases'])} 项检查，链路 IP 帧 {summary['wire_checks']['checked_ipv4_frames']} 个：{output}")


if __name__ == "__main__":
    main()
