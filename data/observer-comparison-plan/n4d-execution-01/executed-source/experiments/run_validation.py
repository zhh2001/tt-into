#!/usr/bin/env python3
"""运行真实 BMv2/Mininet 正确性检查，保存原始轨迹与失败记录。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import traceback

from mininet.log import setLogLevel
from scapy.all import Ether, IP, Raw, TCP, UDP

from build import ROOT, build
from topology import create_chain

sys.path.insert(0, str(ROOT / "src"))
from collector.codec import checksum, decode_frame, TELEMETRY_TYPE

MODULUS = 1 << 48
FLOWS = {1: (17, 10101, 9000), 2: (17, 10102, 9000), 3: (6, 20101, 9001)}
TRACE_NAMES = ["trace_arrival", "trace_gate_time", "trace_decision", "trace_count_before"]


def read_registers(switch, names: list[str], index: int | None = None) -> dict:
    suffix = "" if index is None else " " + str(index)
    output = switch.cli([f"register_read TTEgress.{name}{suffix}" for name in names])
    result = {}
    for name in names:
        match = re.search(r"TTEgress\." + name + r"(?:\[\d+\])?\s*=\s*([^\n]*)", output)
        if not match:
            raise RuntimeError(f"没有读到寄存器 {name}：{output[-500:]}")
        result[name] = [int(value) for value in re.findall(r"\d+", match.group(1))]
        if not result[name]:
            raise RuntimeError(f"寄存器 {name} 的返回为空")
    return result


def configure(switches, hops: int) -> None:
    for node_id, switch in enumerate(switches, 1):
        sink = int(node_id == hops + 1)
        commands = [f"table_set_default TTIngress.node_config set_node {node_id} {int(node_id == 1)} {sink} 1500",
                    f"table_add TTIngress.ipv4_forward forward 10.0.0.2 => 2 02:00:00:00:00:02 02:00:00:10:00:{node_id:02x}",
                    f"table_add TTIngress.ipv4_forward forward 10.0.0.1 => 1 02:00:00:00:00:01 02:00:00:10:00:{node_id:02x}"]
        if sink:
            commands.append("mirroring_add 100 3")
        else:
            for index, (protocol, sport, dport) in FLOWS.items():
                commands.append(f"table_add TTIngress.flow_map assign_flow 10.0.0.1 10.0.0.2 {protocol} {sport} {dport} => {index}")
        switch.cli(commands)


def reset(switches, threshold_us: int, capacity: int) -> None:
    for switch in switches:
        commands = [f"register_reset TTEgress.{name}" for name in
                    ["last_timestamp", "has_sample", "clock_offset", *TRACE_NAMES]]
        for index in FLOWS:
            commands += [f"register_write TTEgress.time_threshold {index} {threshold_us}",
                         f"register_write TTEgress.max_count {index} {capacity}"]
        switch.cli(commands)


def make_packet(packet_id: int, flow: int = 1, ip_length: int = 120,
                offset_ms: int = 0, **extra) -> dict:
    protocol, sport, dport = FLOWS.get(flow, (17, 19999, 9000))
    transport = UDP(sport=sport, dport=dport) if protocol == 17 else TCP(
        sport=sport, dport=dport, flags="PA", seq=packet_id)
    payload_size = ip_length - 20 - len(transport)
    if payload_size < 8:
        raise ValueError("测试有效载荷不足")
    payload = packet_id.to_bytes(4, "big") + bytes([flow]) * (payload_size - 4)
    frame = bytes(Ether(src="02:00:00:00:00:01", dst="02:00:00:00:00:02") /
                  IP(src="10.0.0.1", dst="10.0.0.2", ttl=64, id=packet_id) /
                  transport / Raw(payload))
    result = {"packet_id": packet_id, "flow": flow, "ip_length": ip_length,
              "offset_ns": offset_ms * 1_000_000, "frame_hex": frame.hex(),
              "payload_hex": payload.hex(), "expect_drop": False}
    result.update(extra)
    return result


def regular(count: int, gap_ms: int, **options) -> list[dict]:
    return [make_packet(index + 1, offset_ms=index * gap_ms, **options)
            for index in range(count)]


def send(source, directory: Path, packets: list[dict], phase: str = "main") -> None:
    description = directory / f"packets-{phase}.json"
    description.write_text(json.dumps(packets, indent=2) + "\n")
    command = [sys.executable, str(ROOT / "experiments/packet_io.py"), "send",
               "--iface", source.defaultIntf().name, "--packets", str(description),
               "--events", str(directory / f"send-{phase}.jsonl")]
    process = source.popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    duration = max((packet["offset_ns"] for packet in packets), default=0) / 1e9
    output, error = process.communicate(timeout=duration + 10)
    if process.returncode:
        raise RuntimeError(f"发送失败：{error.decode()[-1000:]}")


def start_capture(host, directory: Path, name: str, serve: bool):
    ready, stop = directory / f"{name}.ready", directory / f"{name}.stop"
    log = open(directory / f"{name}.log", "w")
    command = [sys.executable, str(ROOT / "experiments/packet_io.py"), "capture",
               "--iface", host.defaultIntf().name, "--pcap", str(directory / f"{name}.pcap"),
               "--events", str(directory / f"{name}.jsonl"), "--ready", str(ready),
               "--stop", str(stop)]
    if serve:
        command.append("--serve")
    process = host.popen(command, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 5
    while not ready.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            if process.poll() is None:
                process.terminate(); process.wait(timeout=2)
            log.close()
            raise RuntimeError(f"抓包启动失败，见 {directory / (name + '.log')}")
        time.sleep(0.02)
    return process, stop, log


def finish_capture(handle) -> None:
    process, stop, log = handle
    stop.write_text("stop\n")
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.terminate(); process.wait(timeout=3)
    log.close()
    if process.returncode:
        raise RuntimeError(f"抓包进程异常退出：{process.returncode}")


def load_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def verify_case(directory: Path, packets: list[dict], traces: list[dict],
                states: list[dict], hops: int, threshold_us: int, capacity: int) -> dict:
    errors = []
    destination_events = load_events(directory / "destination.jsonl")
    collector_events = load_events(directory / "collector.jsonl")
    received = {event["packet_id"]: event for event in destination_events
                if event.get("kind") == "frame" and event.get("src") == "10.0.0.1"
                and event.get("dst") == "10.0.0.2"}
    reports = {event["packet_id"]: event for event in collector_events
               if event.get("kind") == "frame" and event.get("ether_type") == TELEMETRY_TYPE}
    udp_payloads = [event["payload_hex"] for event in destination_events
                    if event.get("kind") == "udp_application"]
    last = [{} for _ in range(hops)]
    counts = [0] * hops
    exported_items = 0
    observations = []
    for packet in packets:
        packet_id, flow = packet["packet_id"], packet["flow"]
        if packet["expect_drop"]:
            if packet_id in received or packet_id in reports:
                errors.append(f"拒绝报文 {packet_id} 被转发或导出")
            continue
        original = decode_frame(bytes.fromhex(packet["frame_hex"]))
        received_packet = received.get(packet_id)
        if not received_packet:
            errors.append(f"业务包 {packet_id} 未收到")
            continue
        if (received_packet["ether_type"] != 0x0800 or
            received_packet["ttl"] != 64 - hops - 1 or
            received_packet["ip_length"] != packet["ip_length"] or
            not received_packet["ip_checksum_ok"] or
            received_packet["transport_hex"] != original["transport_hex"]):
            errors.append(f"业务包 {packet_id} 未正确恢复")
        transport = bytes.fromhex(received_packet["transport_hex"])
        pseudo = socket.inet_aton("10.0.0.1") + socket.inet_aton("10.0.0.2") + bytes(
            [0, original["protocol"]]) + len(transport).to_bytes(2, "big")
        if checksum(pseudo + transport) != 0:
            errors.append(f"业务包 {packet_id} 的传输层校验和错误")
        if original["protocol"] == 17 and packet["payload_hex"] not in udp_payloads:
            errors.append(f"业务包 {packet_id} 未交付 UDP 应用")
        threshold = packet.get("threshold_us", threshold_us) or 100000
        capacities = packet.get("capacities", [capacity] * hops)
        active = flow in FLOWS
        envelope = active and packet["ip_length"] <= 1498
        length = packet["ip_length"] + (2 if envelope else 0)
        records = []
        for node, trace in enumerate(traces):
            now = trace["trace_gate_time"][packet_id]
            observed_decision = trace["trace_decision"][packet_id]
            if not trace["trace_arrival"][packet_id]:
                errors.append(f"节点 {node + 1} 未记录报文 {packet_id}")
                continue
            count_before = len(records)
            if active and not envelope:
                decision = 4 if node == 0 else 5
            elif not active:
                decision = 5
            elif flow in last[node] and (now - last[node][flow]) % MODULUS < threshold:
                decision = 2
            elif len(records) >= min(capacities[node], 8):
                decision = 3
            elif length + 2 > 1500:
                decision = 4
            else:
                decision = 1
                records.insert(0, {"switch_id": node + 1, "hop": node + 1})
                last[node][flow] = now
                length += 2
                counts[node] += 1
            if observed_decision != decision:
                errors.append(f"报文 {packet_id} 节点 {node + 1} 决策 {observed_decision} != {decision}")
            if trace["trace_count_before"][packet_id] != count_before:
                errors.append(f"报文 {packet_id} 节点 {node + 1} 的插入前项数错误")
            observations.append({"packet_id": packet_id, "node_id": node + 1, "flow": flow,
                                 "arrival_time_us": trace["trace_arrival"][packet_id],
                                 "gate_time_us": now, "threshold_us": threshold,
                                 "capacity": capacities[node], "count_before": count_before,
                                 "decision": observed_decision, "expected_decision": decision})
        report = reports.get(packet_id)
        if records:
            if not report or report["records"] != records or report["count"] != len(records):
                errors.append(f"报文 {packet_id} 导出记录与独立重放不符")
            elif (report["initial_ttl"] != 64 or not report["ip_checksum_ok"] or
                  report["frame_length"] != 36 + 2 * len(records) or
                  report["transport_hex"]):
                errors.append(f"报文 {packet_id} 的克隆截断或头部错误")
            exported_items += len(records)
        elif report:
            errors.append(f"报文 {packet_id} 出现空报告")
    for node, state in enumerate(states):
        for flow in FLOWS:
            valid = int(flow in last[node])
            if state["has_sample"][flow] != valid:
                errors.append(f"节点 {node + 1} 流 {flow} 的有效位错误")
            if state["last_timestamp"][flow] != last[node].get(flow, 0):
                errors.append(f"节点 {node + 1} 流 {flow} 的最后成功时间被错误更新")
    expected_ids = {packet["packet_id"] for packet in packets if not packet["expect_drop"]}
    if len(received) != len(expected_ids):
        errors.append("目的端存在缺失、额外或重复报文，需核对原始抓包")
    frame_events = [event for event in destination_events if event.get("kind") == "frame"
                    and event.get("src") == "10.0.0.1" and event.get("dst") == "10.0.0.2"]
    if len(frame_events) != len(received):
        errors.append("目的端出现重复包")
    report_events = [event for event in collector_events if event.get("kind") == "frame"
                     and event.get("ether_type") == TELEMETRY_TYPE]
    if len(report_events) != len(reports):
        errors.append("采集端出现重复报告")
    (directory / "observations.jsonl").write_text("".join(json.dumps(row) + "\n" for row in observations))
    return {"status": "passed" if not errors else "failed", "errors": errors,
            "input_packets": len(packets), "received_packets": len(received),
            "expected_drops": sum(packet["expect_drop"] for packet in packets),
            "report_packets": len(reports), "exported_items": exported_items,
            "samples_per_node": counts, "hops": hops, "threshold_us": threshold_us,
            "capacity": capacity, "diagnostics_enabled": True}


def run_case(output: Path, source, destination, collector, sampling,
             name: str, packets: list[dict], threshold_us: int, capacity: int,
             special: str | None = None) -> dict:
    directory = output / name
    directory.mkdir()
    reset(sampling, threshold_us, capacity)
    captures = []
    extra = {}
    try:
        captures.append(start_capture(destination, directory, "destination", True))
        captures.append(start_capture(collector, directory, "collector", False))
        if special in ("capacity-release", "threshold-update"):
            split = 5 if special == "capacity-release" else 3
            first, second = packets[:split], packets[split:]
            send(source, directory, first, "before")
            before = read_registers(sampling[0], ["last_timestamp", "has_sample"], 1)
            if special == "capacity-release":
                sampling[0].cli(["register_write TTEgress.max_count 1 0"])
            else:
                for switch in sampling:
                    switch.cli(["register_write TTEgress.time_threshold 1 50000"])
            after = read_registers(sampling[0], ["last_timestamp", "has_sample"], 1)
            extra["state_preserved_on_update"] = before == after
            send(source, directory, second, "after")
        elif special == "wrap":
            probe = make_packet(4000, flow=0)
            send(source, directory, [probe], "probe")
            probe_wall = load_events(directory / "send-probe.jsonl")[0]["send_before_ns"]
            anchors = [read_registers(switch, ["trace_arrival"], 4000)["trace_arrival"][0]
                       for switch in sampling]
            target = time.time_ns() + 4_000_000_000
            for switch, anchor in zip(sampling, anchors):
                offset = (MODULUS - anchor - (target - probe_wall) // 1000) % MODULUS
                switch.cli([f"register_write TTEgress.clock_offset 0 {offset}"])
            send(source, directory, packets)
            packets = [probe] + packets
            extra["wrap_kind"] = "诊断时间偏移使实际门控算术跨越 48 位回绕，不是等待真实时钟回绕"
        elif special == "tcp-stream":
            payload = b"TT-INT transport verification\n" * 1000
            process = source.popen([sys.executable, str(ROOT / "experiments/packet_io.py"),
                                   "tcp-send", "--payload", payload.hex()],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate(timeout=8)
            if process.returncode:
                raise RuntimeError("TCP 应用发送失败：" + stderr.decode()[-1500:])
            time.sleep(0.5)
            for handle in captures:
                finish_capture(handle)
            captures = []
            events = load_events(directory / "destination.jsonl")
            received = b"".join(bytes.fromhex(event["payload_hex"]) for event in events
                                if event.get("kind") == "tcp_application")
            return {"name": name, "status": "passed" if received == payload else "failed",
                    "errors": [] if received == payload else ["TCP 应用内容不一致"],
                    "sent_application_bytes": len(payload), "received_application_bytes": len(received),
                    "diagnostics_enabled": True, "hops": len(sampling)}
        else:
            send(source, directory, packets)
            if special == "silence":
                first = read_registers(sampling[0], ["last_timestamp"], 1)
                time.sleep(0.4)
                second = read_registers(sampling[0], ["last_timestamp"], 1)
                extra["no_clock_update_without_packets"] = first == second
        time.sleep(0.15)
        traces = [read_registers(switch, TRACE_NAMES) for switch in sampling]
        states = [read_registers(switch, ["last_timestamp", "has_sample"]) for switch in sampling]
        (directory / "registers.json").write_text(json.dumps({"traces": traces, "states": states}) + "\n")
        for handle in captures:
            finish_capture(handle)
        captures = []
        result = verify_case(directory, packets, traces, states, len(sampling), threshold_us, capacity)
        result.update(name=name, **extra)
        if special == "wrap":
            result["wrapped_nodes"] = [index + 1 for index, trace in enumerate(traces)
                if any(trace["trace_gate_time"][right["packet_id"]] < trace["trace_gate_time"][left["packet_id"]]
                       for left, right in zip(packets[1:], packets[2:]))]
            if len(result["wrapped_nodes"]) != len(sampling):
                result["errors"].append("时间偏移测试未在全部节点跨越回绕")
        if special in ("capacity-release", "threshold-update") and not extra["state_preserved_on_update"]:
            result["errors"].append("在线配置更新重置了成功时间")
        if special == "silence" and not extra["no_clock_update_without_packets"]:
            result["errors"].append("没有业务包时成功时间发生变化")
        if name == "dense_capacity_1" and not all(result["samples_per_node"]):
            result["errors"].append("密集机会场景没有覆盖全部采样节点")
        if name == "sparse_capacity_1" and result["samples_per_node"] != [len(packets)] + [0] * (len(sampling) - 1):
            result["errors"].append("没有复现稀疏机会下的容量饥饿")
        result["status"] = "passed" if not result["errors"] else "failed"
        return result
    finally:
        for handle in captures:
            try:
                finish_capture(handle)
            except Exception:
                pass


def cases(hops: int) -> list[tuple]:
    release = regular(5, 200) + [make_packet(index + 6, offset_ms=index * 200,
                    capacities=[0] + [1] * (hops - 1)) for index in range(3)]
    update = regular(3, 40) + [make_packet(index + 4, offset_ms=index * 80,
                                       threshold_us=50000) for index in range(3)]
    isolation = [make_packet(index + 1, flow=index % 3 + 1, offset_ms=index * 10)
                 for index in range(6)]
    bad = []
    for index, kind in enumerate(["count", "checksum", "ttl", "short"], 1):
        packet = make_packet(index, expect_drop=True)
        frame = bytearray.fromhex(packet["frame_hex"])
        if kind == "checksum":
            frame[24] ^= 0xFF
        elif kind == "ttl":
            frame[22] = 1; frame[24:26] = b"\0\0"
            frame[24:26] = checksum(bytes(frame[14:34])).to_bytes(2, "big")
        else:
            payload = bytes([9 if kind == "count" else 8, 64]) + bytes([1, 1])
            frame = bytearray(bytes(Ether(src="02:00:00:00:00:01", dst="02:00:00:00:00:02", type=TELEMETRY_TYPE) /
                              IP(src="10.0.0.1", dst="10.0.0.2", id=index, ttl=64, proto=17) / Raw(payload)))
        packet.update(frame_hex=frame.hex(), malformed_kind=kind)
        bad.append(packet)
    return [
        ("unregistered_forwarding", regular(6, 10, flow=0), 100000, hops, None),
        ("udp_full_path", regular(8, 200), 100000, hops, None),
        ("tcp_raw_full_path", regular(6, 200, flow=3), 100000, hops, None),
        ("sparse_capacity_1", regular(16, 200), 100000, 1, None),
        ("sparse_capacity_2", regular(8, 200), 100000, 2, None),
        ("dense_capacity_1", regular(121, 10), 100000, 1, None),
        ("five_tuple_isolation", isolation, 1000000, hops, None),
        ("no_packet_no_report", regular(1, 0), 100000, hops, "silence"),
        ("mtu_envelope_guard", [make_packet(index + 1, ip_length=size, offset_ms=index * 200)
                                for index, size in enumerate([1497, 1498, 1499, 1500])], 100000, hops, None),
        ("mtu_item_guard", regular(4, 200, ip_length=1496), 100000, hops, None),
        ("capacity_release", release, 100000, 1, "capacity-release"),
        ("threshold_update", update, 1000000, hops, "threshold-update"),
        ("timestamp_wrap", regular(31, 200), 300000, hops, "wrap"),
        ("default_threshold", regular(6, 200), 0, hops, None),
        ("malformed_rejected", bad, 100000, hops, None),
        ("tcp_application_stream", [], 100000, hops, "tcp-stream"),
    ]


def software_version(command) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    return (result.stdout + result.stderr).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hops", type=int, choices=[3, 5], default=5,
                        help="采样节点数量，另设一个只导出的 sink")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cases", nargs="+", help="只运行指定检查")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Mininet 与原始抓包需要 root，请使用 sudo 运行，不在参数中传密码")
    setLogLevel("warning")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or ROOT / "data/validation-runs" / stamp).resolve()
    output.mkdir(parents=True, exist_ok=False)
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
              for folder in ["src", "experiments"] for path in sorted((ROOT / folder).rglob("*"))
              if path.is_file() and path.suffix in (".py", ".p4")}
    manifest = {"run_id": output.name, "start_utc": stamp, "hops": args.hops,
                "sink_nodes": 1, "source_sha256": hashes, "seed": 0,
                "traffic_kind": "确定性固定序列，按实际到包时间重放核对",
                "warmup": "不排除首包，显式检查冷启动",
                "scope": "软件正确性验证，非吞吐或硬件性能评价",
                "versions": {"p4c": software_version(["p4c-bm2-ss", "--version"]),
                             "bmv2": software_version(["simple_switch", "--version"]),
                             "python": sys.version.split()[0],
                             "mininet": software_version(["mn", "--version"])},
                "start_load_average": list(os.getloadavg()),
                "status": "running", "cases": []}
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    network = None
    try:
        program = build(True)
        base_port = 18000 + (os.getpid() % 1000) * 10
        prefix = f"tv{os.getpid() % 65536:04x}"
        network, source, destination, collector, switches = create_chain(
            program, output, args.hops, prefix, base_port, os.getpid() * 10)
        configure(switches, args.hops)
        print(f"网络已启动：{args.hops} 个采样节点，1 个独立 sink", flush=True)
        selected = cases(args.hops)
        if args.cases:
            known = {item[0] for item in selected}
            if set(args.cases) - known:
                raise ValueError("未知检查名称：" + str(set(args.cases) - known))
            selected = [item for item in selected if item[0] in args.cases]
        for name, packets, threshold, capacity, special in selected:
            started = time.monotonic()
            result = run_case(output, source, destination, collector, switches[:-1],
                              name, packets, threshold, capacity, special)
            result["wall_seconds"] = time.monotonic() - started
            manifest["cases"].append(result)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            print(f"{name}: {result['status']}，采样数 {result.get('samples_per_node', [])}", flush=True)
            if result["errors"]:
                print(json.dumps(result["errors"][:8], ensure_ascii=False), flush=True)
        manifest["status"] = "passed" if all(row["status"] == "passed" for row in manifest["cases"]) else "failed"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failure"] = str(exc)
        (output / "failure.txt").write_text(traceback.format_exc())
        print(f"验证失败：{exc}", flush=True)
    finally:
        if network is not None:
            network.stop()
        manifest["finish_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["finish_load_average"] = list(os.getloadavg())
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"原始记录：{output}", flush=True)
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
