/* TT-INTO 身份格式及原有逐流时间门控，独立版本 6。 */
#include <core.p4>
#include <v1model.p4>

const bit<16> IPV4_TYPE = 0x0800;
const bit<16> TELEMETRY_TYPE = 0x88b6;
const bit<32> FLOW_SLOTS = 256;
const bit<32> STACK_SLOTS = 8;
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
    bit<32> flow_tag; bit<32> session;
}
header record_t {
    bit<8> switch_id; bit<8> hop; bit<48> generated_us;
    bit<32> queue_packets;
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
    bit<32> flow_tag; bit<32> session;
    bit<8> node_id; bit<1> source;
    @field_list(1) bit<1> sink;
    bit<16> mtu;
    bit<1> mtu_blocked;
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
        verify(hdr.telemetry.version == 6 && hdr.telemetry.metric == 1,
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
#ifdef ENABLE_DIAGNOSTICS
    register<bit<32>>(TRACE_SLOTS) trace_source_count;
#endif
    action discard() { mark_to_drop(sm); }
    action set_node(bit<8> node_id, bit<1> source, bit<1> sink, bit<16> mtu) {
        meta.node_id = node_id; meta.source = source;
        meta.sink = sink; meta.mtu = mtu;
    }
    table node_config {
        actions = { set_node; }
        default_action = set_node(0, 0, 0, 1500);
    }
    action assign_flow(bit<32> index, bit<32> tag, bit<32> session) {
        meta.flow_index = index; meta.flow_tag = tag; meta.session = session;
        if (index < FLOW_SLOTS) { meta.enabled = 1; }
    }
    table flow_map {
        key = { hdr.ipv4.src: exact; hdr.ipv4.dst: exact;
                hdr.ipv4.protocol: exact; meta.sport: exact; meta.dport: exact; }
        actions = { assign_flow; NoAction; }
        size = 256; default_action = NoAction();
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
            if (hdr.telemetry.isValid() &&
                (hdr.telemetry.flow_tag != meta.flow_tag || hdr.telemetry.session != meta.session)) {
                meta.enabled = 0;
            }
            if (meta.source == 1 && meta.enabled == 1) {
                bit<32> count;
                packet_count.read(count, meta.flow_index);
                count = count + 1;
                packet_count.write(meta.flow_index, count);
#ifdef ENABLE_DIAGNOSTICS
                if ((bit<32>) hdr.ipv4.identification < TRACE_SLOTS) {
                    trace_source_count.write((bit<32>) hdr.ipv4.identification, count);
                }
#endif
            }
            if (meta.source == 1 && meta.enabled == 1 &&
                !hdr.telemetry.isValid()) {
                if (hdr.ipv4.total_len <= meta.mtu - 12) {
                    hdr.telemetry.setValid(); hdr.telemetry.count = 0;
                    hdr.telemetry.init_ttl = hdr.ipv4.ttl;
                    hdr.telemetry.version = 6; hdr.telemetry.metric = 1;
                    hdr.telemetry.flow_tag = meta.flow_tag; hdr.telemetry.session = meta.session;
                    hdr.ethernet.ether_type = TELEMETRY_TYPE;
                    hdr.ipv4.total_len = hdr.ipv4.total_len + 12;
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
    register<bit<48>>(FLOW_SLOTS) time_threshold;
    register<bit<48>>(FLOW_SLOTS) last_timestamp;
    register<bit<8>>(FLOW_SLOTS) max_count;
    // 首个合法包立即采样，有效位是对论文未明确初始化的显式约定。
    register<bit<1>>(FLOW_SLOTS) has_sample;
#ifdef ENABLE_DIAGNOSTICS
    // 正确性诊断另计状态及访问成本，性能评价应关闭这些逐包记录。
    register<bit<48>>(TRACE_SLOTS) trace_arrival;
    register<bit<48>>(TRACE_SLOTS) trace_gate_time;
    register<bit<8>>(TRACE_SLOTS) trace_decision;
    register<bit<8>>(TRACE_SLOTS) trace_count_before;
    register<bit<48>>(1) clock_offset;
    register<bit<48>>(TRACE_SLOTS) trace_generated;
    register<bit<32>>(TRACE_SLOTS) trace_value;
    register<bit<48>>(TRACE_SLOTS) trace_threshold;
    register<bit<48>>(TRACE_SLOTS) trace_previous;
    register<bit<1>>(TRACE_SLOTS) trace_valid;
    register<bit<8>>(TRACE_SLOTS) trace_capacity;
    register<bit<32>>(TRACE_SLOTS) trace_flow_index;
    register<bit<1>>(TRACE_SLOTS) trace_registered;
    register<bit<1>>(TRACE_SLOTS) trace_telemetry;
    register<bit<16>>(TRACE_SLOTS) trace_ip_length;
    register<bit<16>>(TRACE_SLOTS) trace_mtu;
#endif
    apply {
        if (sm.instance_type == INGRESS_CLONE) {
            hdr.ipv4.total_len = 20 + 12 + 12 * (bit<16>) hdr.telemetry.count;
            truncate(14 + (bit<32>) hdr.ipv4.total_len);
        } else if (hdr.ipv4.isValid()) {
            if (meta.sink == 1) {
                if (hdr.telemetry.isValid()) {
                    hdr.ipv4.total_len = hdr.ipv4.total_len - 12 -
                        12 * (bit<16>) hdr.telemetry.count;
                    hdr.telemetry.setInvalid(); hdr.records.pop_front(8);
                    hdr.ethernet.ether_type = IPV4_TYPE;
                }
            } else {
                bit<48> now = sm.ingress_global_timestamp;
                bit<8> decision = 5;
                bit<8> count_before = 0;
                bit<48> threshold = 0; bit<48> previous = 0;
                bit<1> valid = 0; bit<8> capacity = 0;
#ifdef ENABLE_DIAGNOSTICS
                bit<16> original_length = hdr.ipv4.total_len;
#endif
#ifdef ENABLE_DIAGNOSTICS
                bit<48> offset;
                clock_offset.read(offset, 0);
                now = now + offset;
#endif
                if (hdr.telemetry.isValid()) { count_before = hdr.telemetry.count; }
                if (meta.mtu_blocked == 1) {
                    decision = 4;
                } else if (meta.enabled == 1 && hdr.telemetry.isValid()) {
                    time_threshold.read(threshold, meta.flow_index);
                    if (threshold == 0) { threshold = DEFAULT_THRESHOLD_US; }
                    last_timestamp.read(previous, meta.flow_index);
                    has_sample.read(valid, meta.flow_index);
                    max_count.read(capacity, meta.flow_index);
                    if (meta.node_id == 0 || meta.node_id > 8) {
                        decision = 6;
                    } else if (valid == 0 || now - previous >= threshold) {
                        if (hdr.telemetry.count >= capacity ||
                            hdr.telemetry.count >= (bit<8>) STACK_SLOTS) {
                            decision = 3;
                        } else if (hdr.ipv4.total_len > meta.mtu - 12) {
                            decision = 4;
                        } else {
                            hdr.records.push_front(1);
                            hdr.records[0].setValid();
                            hdr.records[0].switch_id = meta.node_id;
                            hdr.records[0].generated_us = sm.egress_global_timestamp;
                            hdr.records[0].queue_packets = (bit<32>) sm.deq_qdepth;
                            hdr.records[0].hop = hdr.telemetry.init_ttl - hdr.ipv4.ttl;
                            hdr.telemetry.count = hdr.telemetry.count + 1;
                            hdr.ipv4.total_len = hdr.ipv4.total_len + 12;
                            last_timestamp.write(meta.flow_index, now);
                            has_sample.write(meta.flow_index, 1);
                            decision = 1;
                        }
                    } else { decision = 2; }
                }
#ifdef ENABLE_DIAGNOSTICS
                // 首版诊断只记录前向端口 2，避免反向控制包覆盖测试索引。
                if (sm.egress_port == 2 &&
                    (bit<32>) hdr.ipv4.identification < TRACE_SLOTS) {
                    trace_generated.write((bit<32>) hdr.ipv4.identification,
                                          sm.egress_global_timestamp);
                    trace_value.write((bit<32>) hdr.ipv4.identification,
                                      (bit<32>) sm.deq_qdepth);
                    trace_arrival.write((bit<32>) hdr.ipv4.identification,
                                        sm.ingress_global_timestamp);
                    trace_gate_time.write((bit<32>) hdr.ipv4.identification, now);
                    trace_decision.write((bit<32>) hdr.ipv4.identification, decision);
                    trace_count_before.write((bit<32>) hdr.ipv4.identification,
                                             count_before);
                    trace_threshold.write((bit<32>) hdr.ipv4.identification, threshold);
                    trace_previous.write((bit<32>) hdr.ipv4.identification, previous);
                    trace_valid.write((bit<32>) hdr.ipv4.identification, valid);
                    trace_capacity.write((bit<32>) hdr.ipv4.identification, capacity);
                    trace_flow_index.write((bit<32>) hdr.ipv4.identification, meta.flow_index);
                    trace_registered.write((bit<32>) hdr.ipv4.identification, meta.enabled);
                    trace_telemetry.write((bit<32>) hdr.ipv4.identification, (bit<1>) hdr.telemetry.isValid());
                    trace_ip_length.write((bit<32>) hdr.ipv4.identification, original_length);
                    trace_mtu.write((bit<32>) hdr.ipv4.identification, meta.mtu);
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
