# TT-INT 软件原型与基线执行验证

最新把冻结分配接到真实收报和 BMv2，普通版 24 个、诊断版 6 个配置检查通过，共交付 2400 包、核查 8880 输出帧及 930 次在线决策，单元检查现为 108 项。正常收报时全部任务履约，导出延迟与丢失下保留年龄违约，四种方法仍选择相同分配。链路损伤未配对，不作性能排名，详见[真实收报复现说明](experiments/README-assignment-validation.md)。

最新增加 D-INTO P3 和 INTOPP 的准确分配参照，单元检查现为 102 项，180 次原模型与独立穷举对照通过。训练与保留种子分开，最终有 35952 次条件事件执行。纳入平局选流调参后，四种方法在 280 组配对输入中行为相同。该结果不是网络测量，范围及源码快照见[分配复现说明](experiments/README-assignment-reference.md)。

上一阶段完成两个固定分支配置的初始化、阈值裕量与容量搭载部署。8 项执行检查交付 640 包，核查 2140 个输出帧，当时单元检查为 77 项。原参数的一次年龄违约完整保留，加入原有间隔裕量后普通与诊断版本均满足各自数据预算和全部期限。安装、启用、阶段更新及全量控制帧分列计费，范围见[部署复现说明](experiments/README-admission-validation.md)。

此前固定轨迹的全部 56 个数据预算点中，原有 TT-INT 初始化、阈值及容量搭载达到相同轨迹的接纳权重上界，共完成 739116 次配置筛查和 125 次接纳接口求解。这是未来已知、未含控制预算的模型结果，不等于全部点已部署，见[接纳复现说明](experiments/README-admission.md)。

此前项目独立的 BMv2 单调时钟构建、23 项正常及跳变网络检查，以及格式、源端封装和固定控制账本的独立离线接口，见[时钟与成本说明](experiments/README-clock-budget.md)。失败的旧时钟阳性对照完整保留。

基于 [TT-INT 论文](https://doi.org/10.1109/TNSM.2026.3688086)描述独立实现时间阈值核心，用于 BMv2、Mininet 环境下的机制验证。当前包含固定路径转发、五元组到寄存器索引的映射、逐流时间门控、包容量检查，以及独立 sink 的克隆导出和原包恢复。

首轮已经完成三节点与五节点各 16 项网络检查，以及关闭诊断后的三节点 UDP 检查。采集器另有 7 项单元检查。数据来自单次确定性场景验证，不用于估计吞吐、硬件性能或算法改善比例。

## 实现约定

| 项目 | 当前约定 |
| --- | --- |
| 数据平面 | P4_16、V1Model、BMv2 `simple_switch` |
| 转发 | IPv4，无 IP 选项，静态双向路由及端点 ARP |
| 流索引 | 精确匹配源/目的 IP、协议、源/目的端口，控制端预先分配不同索引 |
| 基础头 | `Count` 与 `InitTTL` 各 8 位，共 2 B |
| 记录 | `SwitchID` 与 `HopNum` 各 8 位，共 2 B，插入到栈首 |
| 容量 | 编译上限 8 项，实际由每节点、每流的 `max_count` 进一步限制 |
| 时间门控 | 使用到达 ingress 的本地时间，达到阈值后在 egress 插入，仅成功插入才更新状态 |
| 首包 | 首个合法且有容量的包立即采样，通过额外 1 位有效状态区分初始化 |
| 默认阈值 | 寄存器值为 0 时使用 100000 μs，不能用 0 表示关闭采样 |
| sink | 单独的转发节点，不插入新记录，非空遥测包克隆到采集端，原包剥离后继续转发 |

域内标记使用 `0x88B5`，该 EtherType 在 [IANA 登记表](https://www.iana.org/assignments/ieee-802-numbers)中属于本地实验类型，不增加报文字段。IPv4 的传输协议值保持不变。基础头置于 IPv4 与 UDP/TCP 之间，插入或剥离时调整 IPv4 长度与校验和。端点收到恢复后的普通 IPv4 包，传输层数据与原校验和保持一致。

源节点为已注册且有基础头空间的包添加 2 B 基础头，即使本包最终没有记录也承担此开销。没有空间时保留普通包。空遥测包不生成报告。每次插入额外检查链路 MTU，首版不执行分片。未知流正常转发，不自动注册。

源节点先记录原始 TTL，再做普通转发的 TTL 递减，因此第一个采样节点的 HopNum 为 1。`--hops 5` 表示 5 个采样节点和另一个独立 sink，业务包实际经过 6 个转发节点。测试期望接收 TTL 为 58。

sink 报告仅包含 Ethernet、IPv4、基础头及记录。报告的 IPv4 长度与校验和按截断后内容更新，其中没有完整传输层。当前记录用于路径与门控验证，不包含队列状态、生成时间或完整五元组。受控测试通过唯一的 IPv4 ID 与输入清单关联报告，不能把这种关联直接用于任意生产流量。

## 环境和运行

已验证环境为 p4c 1.2.5.10、BMv2 1.15.0、Mininet 2.3.0、Python 3.12.3、Scapy 2.5.0、Matplotlib 3.6.3。系统需要 `p4c-bm2-ss`、`simple_switch`、`simple_switch_CLI`、`mn`、`ethtool`，以及 Python 的 Mininet、Scapy 和 Matplotlib 模块。绘图需要真正的 Times New Roman 字体。

在项目根目录编译和运行采集器检查：

```bash
python3 experiments/build.py
python3 experiments/build.py --diagnostics
python3 -m unittest discover -s tests -v
```

运行网络验证需要 root，以创建网络命名空间、veth 和原始抓包套接字：

```bash
sudo python3 experiments/run_validation.py --hops 5
sudo python3 experiments/run_validation.py --hops 3
sudo python3 experiments/run_smoke.py --hops 3 \
  --output data/validation-runs/ordinary-check
```

`run_validation.py` 默认按 UTC 时间建立新的原始记录目录，也可用 `--output` 指定不存在的目录。通过 `--cases udp_full_path sparse_capacity_1` 可仅运行指定检查。重复运行不要覆盖原始目录。

脚本只结束自己启动的进程和网络，不调用全局 `mn -c`。原始目录保留各链路 PCAP、控制操作、收发事件、寄存器和运行清单。运行清单记录源码 SHA-256、软件版本、参数、种子、冷启动处理、主机负载及成功或失败状态。失败记录保留，不自动重试或丢弃。

## 诊断与独立核对

普通版本只保留核心状态和首包有效位。诊断版本额外记录前向端口 2 的逐包到达时间、门控时间、插入前项数和决策。固定测试使用小于 4096 的唯一 IPv4 ID 索引这些记录，反向包不覆盖记录。

诊断记录的逻辑宽度为每包 14 B，共 4096 槽，另有 6 B 时间偏移量。核心每流状态为 104 位，加首包有效位后为 105 位。这些是字段宽度合计，不等于 BMv2 实际进程内存，更不代表硬件资源消耗。逐包诊断改变软件访问成本，不能用诊断版本做吞吐比较。

检查器以输入清单、实测本地时间和配置独立重放门控及容量约束，再核对实际导出记录、恢复报文、UDP 应用内容和最终成功时间。容量阻挡、MTU 阻挡及未到阈值的包均不得更新成功时间。TCP 同时检查受控原始段恢复和实际应用流传输。

48 位回绕检查通过诊断版的时间偏移使实际门控运算跨越回绕，普通版本没有这一偏移接口。这不是等待 BMv2 真实时钟运行到回绕。普通 BMv2 时间从各进程启动的零点起计，详见[官方时间戳说明](https://github.com/p4lang/behavioral-model/blob/main/docs/simple_switch.md#bmv2-timestamp-implementation-notes)。当前没有直接相减不同交换机时间，也没有输出跨设备年龄指标。

## 已保存的验证数据

- [五采样节点检查](data/validation/core-5hop-01/summary.json)
- [三采样节点检查](data/validation/core-3hop-01/summary.json)
- [普通版本检查](data/validation/ordinary-3hop-02/summary.json)
- [五节点插入计数 CSV](data/validation/core-5hop-01/sample-counts.csv)
- [验证图 PNG](figures/validation-5hop/tt-int-core-validation.png)和 [PDF](figures/validation-5hop/tt-int-core-validation.pdf)

五节点场景中，100 ms 阈值、200 ms 包间隔、K=1 的 16 个包产生逐节点计数 `[16, 0, 0, 0, 0]`。容量为全路径时，8 个包产生 `[8, 8, 8, 8, 8]`。10 ms 包间隔、K=1 的 121 个包产生 `[12, 12, 12, 12, 11]`。各场景输入数量和时长不同，图只展示构造场景下的插入行为，不作为等预算性能比较。

导出数据及绘图由同一脚本生成：

```bash
python3 experiments/analyze_validation.py \
  --run data/validation-runs/本次运行目录 \
  --output data/validation/本次运行目录 \
  --figures figures/本次运行目录
```

对同一运行重生成派生目录可添加 `--refresh`。脚本校验实际测量所用源文件、链路校验和和 MTU，输出采集 PCAP、输入包、收发事件、逐包诊断及对应 SHA-256。`source_sha256` 是测量开始时的源码快照摘要，`verified_measurement_sources` 列明实际参与测量并核对的文件，`analysis_sha256` 单独记录派生分析版本。图统一使用 Times New Roman，PNG 为 300 dpi，PDF 嵌入字体。

当前最小核心没有时间阈值的网络级优化、共享端口门控、蓄水池选择或动态流索引回收，也没有复现原论文的全部实验数值。

## 共同状态扩展与离线参照

新增的 `state_int.p4` 使用独立的版本 1 格式，输出队列快照和 egress 生成时间。普通版和诊断版各完成 90 个业务包检查。另实现连续年龄积分、六种事件参考策略和固定阈值 MILP，详见[扩展复现说明](experiments/README-reference.md)。其中部分机制随后在下述独立执行器中验证，六种事件策略尚未全部部署到 BMv2。原始最小核心的实现和数据保持独立。

同相和错相两个合成场景中，逐节点调参后的共享门控与静态流授权均可满足全部三个任务。因此这些构造场景不提供新增算法优于强对照的证据。新增图为事件预实验结果，不能与真实网络测量混用。

## 简单基线的真实执行

`baseline_int.p4` 在共同状态格式上启用共享端口门控、源端静态许可和版本更新，并加入 TT-INT 原有逐流阈值与容量参数的调参对照。该阶段诊断版完成 9 项、普通版完成 5 项网络检查，共交付 506 个业务与标定包，核查 2522 个输出 IPv4 帧。当时全部单元检查为 27 项。

有效年龄测量中，共享门控、静态许可和 TT-INT 逐流调参均能满足简单场景的三个任务。TT-INT 调参使用节点上的逐流容量 0 禁止采样，无须源端掩码实现同样分配。这些结果用于排除简单解释，不构成新算法的性能结论。两项运行的宿主时钟变化超过预设条件，保留其转发与成本数据，年龄指标标为不可评估。

实现、参数、时钟条件及成本口径见[基线执行复现说明](experiments/README-baselines.md)。公开测量见[诊断版摘要](data/baseline-validation/baseline-core-02/summary.json)和[普通版摘要](data/baseline-validation/baseline-ordinary-02/summary.json)。实测图提供 [PNG](figures/baseline-validation/baseline-execution.png) 和 [PDF](figures/baseline-validation/baseline-execution.pdf)。

## 间隔筛选、反馈与格式对照

独立的 `strong_int.p4` 增加到包间隔偏差 Dev、源端 1:N、端口布尔反馈及 K=1 的均匀与整数加权流式蓄水池。控制端使用持久 Thrift 连接，按实际计数筛选流并更新采样比率，反馈由实际收到的报告触发。它是 INTDirector 和 INT-label 部分机制的固定路径适配，完整路径扩展、发现与策略优化尚未复现。

四种格式的诊断版及任务精简格式的普通版共完成 46 项网络检查，完整交付 9450 包，核查 44704 个输出 IPv4 帧。全部单元检查现为 36 项。年龄采用同一宿主单调时钟的发送与接收因果区间，保留前史及冷启动未知状态，不依赖跨交换机时间戳相减。

相同插入分配的四种格式分别为 3144、2520、2232、2136 byte-hop 的捕获开销，另列 Ethernet 最小帧长补齐的推导值。格式缩减不构成编排创新，反馈增加采样也同时增加实际成本。实现边界、复现步骤和数据入口见[固定路径适配说明](experiments/README-strong.md)。图提供[格式与蓄水池 PNG](figures/strong-validation/strong-format-and-reservoir.png)、[PDF](figures/strong-validation/strong-format-and-reservoir.pdf)及[反馈轨迹 PNG](figures/strong-validation/strong-feedback-timeline.png)、[PDF](figures/strong-validation/strong-feedback-timeline.pdf)。
