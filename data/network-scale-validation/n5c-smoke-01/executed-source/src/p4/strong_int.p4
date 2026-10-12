/* 固定路径适配：到包偏差、1:N、布尔反馈与 K=1 流式蓄水池。 */
#include <core.p4>
#include <v1model.p4>
#ifndef WIRE_FORMAT
#define WIRE_FORMAT 0
#endif
#if WIRE_FORMAT == 0
#define HEADER_SIZE 8
#define RECORD_SIZE 20
#define VERSION_ID 2
#elif WIRE_FORMAT == 1
#define HEADER_SIZE 8
#define RECORD_SIZE 12
#define VERSION_ID 3
#elif WIRE_FORMAT == 2
#define HEADER_SIZE 5
#define RECORD_SIZE 12
#define VERSION_ID 4
#else
#define HEADER_SIZE 4
#define RECORD_SIZE 12
#define VERSION_ID 5
#endif

const bit<16> IPV4_TYPE = 0x0800;
const bit<16> TELEMETRY_TYPE = 0x88b6;
const bit<32> FLOW_SLOTS = 256;
const bit<32> STACK_SLOTS = 8;
const bit<32> GATE_SLOTS = 768;
#ifdef ENABLE_DIAGNOSTICS
const bit<32> TRACE_SLOTS = 4096;
#endif
const bit<32> REPORT_SESSION = 100;
const bit<32> INGRESS_CLONE = 1;
const bit<48> DEFAULT_THRESHOLD_US = 100000;

error { UnsupportedIPv4, BadTelemetryCount, BadStateVersion }

header ethernet_t { bit<48> dst; bit<48> src; bit<16> ether_type; }
header ipv4_t {
    bit<4> version; bit<4> ihl; bit<8> diffserv; bit<16> total_len;
    bit<16> identification; bit<3> flags; bit<13> frag_offset;
    bit<8> ttl; bit<8> protocol; bit<16> checksum;
    bit<32> src; bit<32> dst;
}
header int_t {
    bit<8> count; bit<8> init_ttl; bit<8> version; bit<8> metric;
#if WIRE_FORMAT < 2
    bit<8> allow_mask;
#endif
#if WIRE_FORMAT < 3
    bit<8> seen;
#endif
#if WIRE_FORMAT < 2
    bit<16> epoch;
#endif
}
header record_t {
    bit<8> switch_id; bit<8> hop; bit<48> generated_us;
    bit<32> queue_packets;
#if WIRE_FORMAT == 0
    bit<64> selection_key;
#endif
}
header udp_t { bit<16> src; bit<16> dst; bit<16> length; bit<16> checksum; }
header tcp_t {
    bit<16> src; bit<16> dst; bit<32> seq; bit<32> ack;
    bit<4> data_offset; bit<4> reserved; bit<8> flags;
    bit<16> window; bit<16> checksum; bit<16> urgent;
}
struct headers_t {
    ethernet_t ethernet; ipv4_t ipv4; int_t telemetry;
    record_t[8] records; udp_t udp; tcp_t tcp;
}
struct metadata_t {
    bit<8> parsed; bit<16> sport; bit<16> dport;
    bit<32> flow_index; bit<1> enabled;
    bit<8> node_id; bit<1> source;
    @field_list(1) bit<1> sink;
    bit<16> mtu;
    bit<1> mtu_blocked; bit<8> source_mask; bit<16> source_epoch;
    bit<1> activated; bit<32> ratio; bit<32> remainder;
}

parser TTParser(packet_in packet, out headers_t hdr, inout metadata_t meta,
                inout standard_metadata_t sm) {
    state start {
        meta.parsed = 0; meta.sport = 0; meta.dport = 0;
        meta.enabled = 0; meta.mtu_blocked = 0;
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.ether_type) {
            IPV4_TYPE: ipv4;
            TELEMETRY_TYPE: ipv4;
            default: accept;
        }
    }
    state ipv4 {
        packet.extract(hdr.ipv4);
        verify(hdr.ipv4.version == 4 && hdr.ipv4.ihl == 5,
               error.UnsupportedIPv4);
        transition select(hdr.ethernet.ether_type) {
            TELEMETRY_TYPE: telemetry;
            default: transport;
        }
    }
    state telemetry {
        packet.extract(hdr.telemetry);
        verify(hdr.telemetry.version == VERSION_ID && hdr.telemetry.metric == 1,
               error.BadStateVersion);
        verify(hdr.telemetry.count <= (bit<8>) STACK_SLOTS,
               error.BadTelemetryCount);
        transition select(hdr.telemetry.count) {
            0: transport;
            default: record;
        }
    }
    state record {
        packet.extract(hdr.records.next);
        meta.parsed = meta.parsed + 1;
        transition select(meta.parsed < hdr.telemetry.count) {
            true: record;
            default: transport;
        }
    }
    state transport {
        transition select(hdr.ipv4.frag_offset == 0 &&
                          (hdr.ipv4.flags & 1) == 0, hdr.ipv4.protocol) {
            (true, 17): udp;
            (true, 6): tcp;
            default: accept;
        }
    }
    state udp {
        packet.extract(hdr.udp);
        meta.sport = hdr.udp.src; meta.dport = hdr.udp.dst;
        transition accept;
    }
    state tcp {
        packet.extract(hdr.tcp);
        meta.sport = hdr.tcp.src; meta.dport = hdr.tcp.dst;
        transition accept;
    }
}

control TTVerify(inout headers_t hdr, inout metadata_t meta) {
    apply {
        verify_checksum(hdr.ipv4.isValid(),
            { hdr.ipv4.version, hdr.ipv4.ihl, hdr.ipv4.diffserv,
              hdr.ipv4.total_len, hdr.ipv4.identification, hdr.ipv4.flags,
              hdr.ipv4.frag_offset, hdr.ipv4.ttl, hdr.ipv4.protocol,
              hdr.ipv4.src, hdr.ipv4.dst }, hdr.ipv4.checksum,
            HashAlgorithm.csum16);
    }
}

control TTIngress(inout headers_t hdr, inout metadata_t meta,
                  inout standard_metadata_t sm) {
    register<bit<32>>(FLOW_SLOTS) packet_count;
    register<bit<48>>(FLOW_SLOTS) arrival_last;
    register<bit<1>>(FLOW_SLOTS) arrival_valid;
    register<bit<64>>(FLOW_SLOTS) arrival_dev;
    register<bit<48>>(FLOW_SLOTS) ideal_interval;
#ifdef ENABLE_DIAGNOSTICS
    register<bit<48>>(TRACE_SLOTS) trace_source_time;
    register<bit<32>>(TRACE_SLOTS) trace_source_count;
    register<bit<64>>(TRACE_SLOTS) trace_source_dev;
    register<bit<32>>(TRACE_SLOTS) trace_source_ratio;
    register<bit<1>>(TRACE_SLOTS) trace_source_active;
    register<bit<1>>(TRACE_SLOTS) trace_source_sampled;
#endif
    action sampling(bit<1> active, bit<32> n) {
        meta.activated = active; meta.ratio = n;
    }
    table sampling_policy {
        key = { meta.flow_index: exact; }
        actions = { sampling; }
        size = 256; default_action = sampling(1, 1);
    }
    action discard() { mark_to_drop(sm); }
    action set_node(bit<8> node_id, bit<1> source, bit<1> sink, bit<16> mtu) {
        meta.node_id = node_id; meta.source = source;
        meta.sink = sink; meta.mtu = mtu;
    }
    table node_config {
        actions = { set_node; }
        default_action = set_node(0, 0, 0, 1500);
    }
    action assign_flow(bit<32> index) {
        meta.flow_index = index; meta.enabled = 1;
    }
    table flow_map {
        key = { hdr.ipv4.src: exact; hdr.ipv4.dst: exact;
                hdr.ipv4.protocol: exact; meta.sport: exact; meta.dport: exact; }
        actions = { assign_flow; NoAction; }
        size = 256; default_action = NoAction();
    }
    action authorize(bit<8> mask, bit<16> epoch) {
        meta.source_mask = mask; meta.source_epoch = epoch;
    }
    table source_policy {
        key = { meta.flow_index: exact; }
        actions = { authorize; }
        size = 256; default_action = authorize(255, 0);
    }
    action forward(bit<9> port, bit<48> dst_mac, bit<48> src_mac) {
        sm.egress_spec = port;
        hdr.ethernet.dst = dst_mac; hdr.ethernet.src = src_mac;
    }
    table ipv4_forward {
        key = { hdr.ipv4.dst: exact; }
        actions = { forward; discard; }
        size = 32; default_action = discard();
    }
    apply {
        node_config.apply();
        if (sm.parser_error != error.NoError || sm.checksum_error != 0 ||
            !hdr.ipv4.isValid()) {
            discard();
        } else if (hdr.ipv4.ttl <= 1 || hdr.ipv4.total_len > meta.mtu) {
            discard();
        } else {
            flow_map.apply();
            if (meta.source == 1 && meta.enabled == 1) {
                bit<32> count; bit<48> previous; bit<48> ideal;
                bit<1> valid; bit<64> dev;
                packet_count.read(count, meta.flow_index);
                arrival_last.read(previous, meta.flow_index);
                arrival_valid.read(valid, meta.flow_index);
                arrival_dev.read(dev, meta.flow_index);
                ideal_interval.read(ideal, meta.flow_index);
                count = count + 1;
                if (valid == 1 && sm.ingress_global_timestamp - previous > ideal) {
                    dev = dev + (bit<64>)(sm.ingress_global_timestamp - previous - ideal);
                }
                packet_count.write(meta.flow_index, count);
                arrival_last.write(meta.flow_index, sm.ingress_global_timestamp);
                arrival_valid.write(meta.flow_index, 1);
                arrival_dev.write(meta.flow_index, dev);
                sampling_policy.apply();
                if (meta.ratio == 0) { meta.ratio = 1; }
                hash(meta.remainder, HashAlgorithm.identity, (bit<32>)0, {count}, meta.ratio);
#ifdef ENABLE_DIAGNOSTICS
                bit<32> idx = (bit<32>)(hdr.ipv4.identification & 4095);
                trace_source_time.write(idx, sm.ingress_global_timestamp);
                trace_source_count.write(idx, count);
                trace_source_dev.write(idx, dev);
                trace_source_ratio.write(idx, meta.ratio);
                trace_source_active.write(idx, meta.activated);
                trace_source_sampled.write(idx, (bit<1>)(meta.activated == 1 && meta.remainder == 0));
#endif
            }
            if (meta.source == 1 && meta.enabled == 1 &&
                meta.activated == 1 && meta.remainder == 0 && !hdr.telemetry.isValid()) {
                if (hdr.ipv4.total_len <= meta.mtu - HEADER_SIZE) {
                    hdr.telemetry.setValid(); hdr.telemetry.count = 0;
                    hdr.telemetry.init_ttl = hdr.ipv4.ttl;
                    hdr.telemetry.version = VERSION_ID; hdr.telemetry.metric = 1;
                    source_policy.apply();
#if WIRE_FORMAT < 2
                    hdr.telemetry.allow_mask = meta.source_mask;
                    hdr.telemetry.epoch = meta.source_epoch;
#endif
#if WIRE_FORMAT < 3
                    hdr.telemetry.seen = 0;
#endif
                    hdr.ethernet.ether_type = TELEMETRY_TYPE;
                    hdr.ipv4.total_len = hdr.ipv4.total_len + HEADER_SIZE;
                } else {
                    meta.mtu_blocked = 1;
                }
            }
            if (meta.sink == 1 && hdr.telemetry.isValid() &&
                hdr.telemetry.count > 0) {
                clone_preserving_field_list(CloneType.I2E, REPORT_SESSION, 1);
            }
            hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
            ipv4_forward.apply();
        }
    }
}

control TTEgress(inout headers_t hdr, inout metadata_t meta,
                 inout standard_metadata_t sm) {
    register<bit<48>>(GATE_SLOTS) time_threshold;
    register<bit<48>>(GATE_SLOTS) last_timestamp;
    register<bit<8>>(FLOW_SLOTS) max_count;
    // 首个合法包立即采样，有效位是对论文未明确初始化的显式约定。
    register<bit<1>>(GATE_SLOTS) has_sample;
    register<bit<1>>(512) force_sampling;
    register<bit<1>>(1) drop_report;
    bit<8> mode; bit<8> weight;
    action select_mode(bit<8> selection_mode, bit<8> node_weight) {
        mode = selection_mode; weight = node_weight;
    }
    table selection_policy {
        actions = { select_mode; }
        default_action = select_mode(0, 1);
    }
    bit<1> shared;
    action choose_scope(bit<1> port_shared) { shared = port_shared; }
    table gate_policy {
        key = { sm.egress_port: exact; }
        actions = { choose_scope; }
        size = 512; default_action = choose_scope(0);
    }
#ifdef ENABLE_DIAGNOSTICS
    // 正确性诊断另计状态及访问成本，性能评价应关闭这些逐包记录。
    register<bit<48>>(TRACE_SLOTS) trace_arrival;
    register<bit<48>>(TRACE_SLOTS) trace_gate_time;
    register<bit<8>>(TRACE_SLOTS) trace_decision;
    register<bit<8>>(TRACE_SLOTS) trace_count_before;
    register<bit<48>>(1) clock_offset;
    register<bit<48>>(TRACE_SLOTS) trace_generated;
    register<bit<32>>(TRACE_SLOTS) trace_value;
    register<bit<32>>(TRACE_SLOTS) trace_gate_index;
    register<bit<8>>(TRACE_SLOTS) trace_mask;
    register<bit<16>>(TRACE_SLOTS) trace_epoch;
    register<bit<32>>(TRACE_SLOTS) trace_draw;
    register<bit<8>>(TRACE_SLOTS) trace_seen;
    register<bit<1>>(TRACE_SLOTS) trace_force;
    register<bit<1>>(TRACE_SLOTS) trace_drop;
#endif
    apply {
        if (sm.instance_type == INGRESS_CLONE) {
            bit<1> dropped;
            drop_report.read(dropped, 0);
#ifdef ENABLE_DIAGNOSTICS
            trace_drop.write((bit<32>)(hdr.ipv4.identification & 4095), dropped);
#endif
            if (dropped == 1) { mark_to_drop(sm); }
            hdr.ipv4.total_len = 20 + HEADER_SIZE + RECORD_SIZE * (bit<16>) hdr.telemetry.count;
            truncate(14 + (bit<32>) hdr.ipv4.total_len);
        } else if (hdr.ipv4.isValid()) {
            if (meta.sink == 1) {
                if (hdr.telemetry.isValid()) {
                    hdr.ipv4.total_len = hdr.ipv4.total_len - HEADER_SIZE -
                        RECORD_SIZE * (bit<16>) hdr.telemetry.count;
                    hdr.telemetry.setInvalid(); hdr.records.pop_front(8);
                    hdr.ethernet.ether_type = IPV4_TYPE;
                }
            } else {
                bit<48> now = sm.ingress_global_timestamp;
                bit<8> decision = 5;
                bit<8> count_before = 0;
                bit<32> gate_index = meta.flow_index;
                bit<32> draw = 0; bit<8> seen = 0; bit<1> forced;
                gate_policy.apply();
                selection_policy.apply();
                force_sampling.read(forced, (bit<32>)sm.egress_port);
                if (shared == 1) { gate_index = FLOW_SLOTS + (bit<32>) sm.egress_port; }
#ifdef ENABLE_DIAGNOSTICS
                bit<48> offset;
                clock_offset.read(offset, 0);
                now = now + offset;
#endif
                if (hdr.telemetry.isValid()) { count_before = hdr.telemetry.count; }
                if (meta.mtu_blocked == 1) {
                    decision = 4;
                } else if (meta.enabled == 1 && hdr.telemetry.isValid()) {
                    bit<48> threshold; bit<48> previous;
                    bit<1> valid; bit<8> capacity;
                    time_threshold.read(threshold, gate_index);
                    if (threshold == 0) { threshold = DEFAULT_THRESHOLD_US; }
                    last_timestamp.read(previous, gate_index);
                    has_sample.read(valid, gate_index);
                    max_count.read(capacity, meta.flow_index);
                    bit<8> mask = 255;
#if WIRE_FORMAT < 2
                    mask = hdr.telemetry.allow_mask;
#endif
                    if (meta.node_id == 0 || meta.node_id > 8 ||
                        ((mask >> (meta.node_id - 1)) & 1) == 0) {
                        decision = 6;
                    } else if (valid == 0 || forced == 1 || now - previous >= threshold) {
                        bit<1> selected = 0; bit<1> replaced = 0;
#if WIRE_FORMAT < 3
                        if (mode != 0 && capacity == 1) {
                            bit<8> increment = 1;
                            if (mode == 2) { increment = weight; }
                            if (hdr.telemetry.count == 0 && hdr.ipv4.total_len > meta.mtu - RECORD_SIZE) {
                                decision = 4;
                            } else if (increment == 0 || increment > 31 || (bit<16>)hdr.telemetry.seen + (bit<16>)increment > 255) {
                                decision = 7;
                            } else {
                                hdr.telemetry.seen = hdr.telemetry.seen + increment;
                                seen = hdr.telemetry.seen;
                                random(draw, (bit<32>)1, (bit<32>)seen);
                                if (hdr.telemetry.count == 0 || draw <= (bit<32>)increment) {
                                    selected = 1;
                                    if (hdr.telemetry.count == 1) { replaced = 1; }
                                } else { decision = 7; }
                            }
                        } else
#endif
                        if (hdr.telemetry.count >= capacity ||
                            hdr.telemetry.count >= (bit<8>) STACK_SLOTS) {
                            decision = 3;
                        } else if (hdr.ipv4.total_len > meta.mtu - RECORD_SIZE) {
                            decision = 4;
                        } else { selected = 1; }
                        if (selected == 1) {
                            if (replaced == 0) { hdr.records.push_front(1); }
                            hdr.records[0].setValid();
                            hdr.records[0].switch_id = meta.node_id;
                            hdr.records[0].generated_us = sm.egress_global_timestamp;
                            hdr.records[0].queue_packets = (bit<32>) sm.deq_qdepth;
#if WIRE_FORMAT == 0
                            hdr.records[0].selection_key = 0;
#endif
                            hdr.records[0].hop = hdr.telemetry.init_ttl - hdr.ipv4.ttl;
                            if (replaced == 0) {
                                hdr.telemetry.count = hdr.telemetry.count + 1;
                                hdr.ipv4.total_len = hdr.ipv4.total_len + RECORD_SIZE;
                            }
                            last_timestamp.write(gate_index, now);
                            has_sample.write(gate_index, 1);
                            decision = 1;
                            if (replaced == 1) { decision = 8; }
                        }
                    } else { decision = 2; }
                }
#ifdef ENABLE_DIAGNOSTICS
                // 首版诊断只记录前向端口 2，避免反向控制包覆盖测试索引。
                if (sm.egress_port == 2) {
                    bit<32> idx = (bit<32>)(hdr.ipv4.identification & 4095);
                    trace_draw.write(idx, draw); trace_seen.write(idx, seen);
                    trace_force.write(idx, forced);
                    trace_gate_index.write(idx, gate_index);
#if WIRE_FORMAT < 2
                    trace_mask.write(idx, hdr.telemetry.isValid() ? hdr.telemetry.allow_mask : 0);
                    trace_epoch.write(idx, hdr.telemetry.isValid() ? hdr.telemetry.epoch : 0);
#else
                    trace_mask.write(idx, 255); trace_epoch.write(idx, 0);
#endif
                    trace_generated.write(idx,
                                          sm.egress_global_timestamp);
                    trace_value.write(idx,
                                      (bit<32>) sm.deq_qdepth);
                    trace_arrival.write(idx,
                                        sm.ingress_global_timestamp);
                    trace_gate_time.write(idx, now);
                    trace_decision.write(idx, decision);
                    trace_count_before.write(idx,
                                             count_before);
                }
#endif
            }
        }
    }
}

control TTChecksum(inout headers_t hdr, inout metadata_t meta) {
    apply {
        update_checksum(hdr.ipv4.isValid(),
            { hdr.ipv4.version, hdr.ipv4.ihl, hdr.ipv4.diffserv,
              hdr.ipv4.total_len, hdr.ipv4.identification, hdr.ipv4.flags,
              hdr.ipv4.frag_offset, hdr.ipv4.ttl, hdr.ipv4.protocol,
              hdr.ipv4.src, hdr.ipv4.dst }, hdr.ipv4.checksum,
            HashAlgorithm.csum16);
    }
}
control TTDeparser(packet_out packet, in headers_t hdr) {
    apply {
        packet.emit(hdr.ethernet); packet.emit(hdr.ipv4);
        packet.emit(hdr.telemetry); packet.emit(hdr.records);
        packet.emit(hdr.udp); packet.emit(hdr.tcp);
    }
}
V1Switch(TTParser(), TTVerify(), TTIngress(), TTEgress(),
         TTChecksum(), TTDeparser()) main;
