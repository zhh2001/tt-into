#!/usr/bin/env python3
"""检查关闭逐包诊断的普通 P4 版本。"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from mininet.log import setLogLevel
from build import ROOT, build
from run_validation import (configure, finish_capture, FLOWS, load_events, regular,
                            send, software_version, start_capture)
from topology import create_chain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hops", type=int, choices=[3, 5], default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("需要通过 sudo 运行")
    setLogLevel("warning")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"run_id": output.name, "start_utc": datetime.now(timezone.utc).isoformat(),
                "hops": args.hops, "sink_nodes": 1, "seed": 0,
                "status": "running", "diagnostics_enabled": False, "cases": [],
                "versions": {"p4c": software_version(["p4c-bm2-ss", "--version"]),
                             "bmv2": software_version(["simple_switch", "--version"]),
                             "python": sys.version.split()[0],
                             "mininet": software_version(["mn", "--version"])},
                "start_load_average": list(os.getloadavg()),
                "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for folder in ["src", "experiments"] for path in sorted((ROOT / folder).rglob("*"))
                    if path.is_file() and path.suffix in (".py", ".p4")},
                "scope": "普通版本的转发、遥测及 UDP 应用检查，不是性能实验"}
    network = None
    captures = []
    try:
        network, source, destination, collector, switches = create_chain(build(False), output,
            args.hops, f"ts{os.getpid() % 65536:04x}", 18000 + os.getpid() % 1000 * 10,
            os.getpid() * 10)
        configure(switches, args.hops)
        for switch in switches[:-1]:
            switch.cli([f"register_write TTEgress.max_count {index} {args.hops}" for index in FLOWS])
        directory = output / "ordinary_udp_smoke"
        directory.mkdir()
        captures = [start_capture(destination, directory, "destination", True),
                    start_capture(collector, directory, "collector", False)]
        packets = regular(4, 250)
        send(source, directory, packets)
        time.sleep(0.15)
        for handle in captures:
            finish_capture(handle)
        captures = []
        destination_events = load_events(directory / "destination.jsonl")
        frames = [row for row in destination_events if row.get("kind") == "frame"
                  and row.get("src") == "10.0.0.1"]
        application = [row["payload_hex"] for row in destination_events
                       if row.get("kind") == "udp_application"]
        reports = [row for row in load_events(directory / "collector.jsonl")
                   if row.get("kind") == "frame"]
        expected_records = [{"switch_id": node, "hop": node}
                            for node in range(args.hops, 0, -1)]
        errors = []
        if len(frames) != 4 or sorted(application) != sorted(packet["payload_hex"] for packet in packets):
            errors.append("普通版本的业务包或应用交付错误")
        if any(row["ether_type"] != 0x0800 or not row["ip_checksum_ok"] or
               row["ttl"] != 63 - args.hops or row["ip_length"] != 120 for row in frames):
            errors.append("普通版本的报文恢复错误")
        if len(reports) != 4 or any(row.get("records") != expected_records or
               row["frame_length"] != 36 + 2 * args.hops or not row["ip_checksum_ok"] for row in reports):
            errors.append("普通版本的遥测或克隆截断错误")
        result = {"name": "ordinary_udp_smoke", "status": "passed" if not errors else "failed",
                  "errors": errors, "hops": args.hops, "threshold_us": 100000,
                  "capacity": args.hops, "input_packets": 4, "received_packets": len(frames),
                  "report_packets": len(reports), "samples_per_node": [len(reports)] * args.hops,
                  "diagnostics_enabled": False}
        manifest["cases"].append(result)
        manifest["status"] = result["status"]
    except Exception as exc:
        manifest.update(status="failed", failure=str(exc))
    finally:
        for handle in captures:
            try:
                finish_capture(handle)
            except Exception:
                pass
        if network:
            network.stop()
        manifest["finish_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["finish_load_average"] = list(os.getloadavg())
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"普通版本检查：{manifest['status']}，原始记录：{output}")
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
