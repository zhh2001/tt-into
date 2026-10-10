# 准确分配参照的真实收报验证

本阶段把已选定的 TT-INT、D-INTO P3、INTOPP 年龄适配及其提前版本接到 BMv2。普通版有 24 个配置检查，诊断版只检查 TT-INT 的六个共同配置，共交付 2400 个业务包，核查 8880 个输出帧和 930 次在线决策。108 项单元检查通过。这些是实现正确性检查，没有统计性能或完整文献复现结论。

入口为 `run_assignment_validation.py`，实收报告适配为 `src/orchestration/live_assignment.py`。P4 使用已有 `strong_int.p4` 的 prefix 普通及诊断版本，没有增加字段。每节点记录 12 B、包头 4 B，sink 恢复业务并导出非空记录。

## 冻结配置与执行边界

参数从 `data/assignment-reference/causal-slots-06/selected-parameters.json` 读取，没有用本轮网络数据调参。所有设置使用该轮到包抖动场景、保留种子 300 的同一源端轨迹，保留实际发送时间与调度偏差。

F1 经过 S1、S3，F2 经过 S2、S3，F3 只经过 S3。每案例包含 800 ms 预热和 2400 ms 测量，三个任务期限为 220、220、260 ms，权重为 2、2、1。费用包含预热及全部业务。

K=1 目标为 S1/F1、S2/F2、S3/F3。K=2 为 S1/F1、S2/F2、S3/F2。授权索引使用阈值 1 us，其余使用容量 0。冷启动预装阈值及关闭容量，三个启用 RPC 均在首个业务包前完成。

控制器每 100 ms 决策，每案例有 31 次在线及一次启动决策。成功门控不在在线阶段重置。冻结参数的在线目标一直没有变化。如果产生变化，运行直接标为失败。本检查没有验证跨节点动态更新、生效延迟或未知需求通知协议。

## 实收状态与时钟

决策器只使用已收到且已能与发送前日志关联的报告。同一节点按生成时间取最新状态，旧报告不会回退缓存，不直接相减不同节点的时钟。

在线偏移区间由 `send_before <= generation <= collector_recvmsg` 逐步求交，使用决策截止前可见的记录。年龄上界用于过期判断，未知状态视为到期。时间戳回绕或固定偏移不兼容导致失败。

本机发送日志是验证仪表，尚未实现远程时钟协议或生产观察链路。文件读取和求解属于宿主计算。本轮没有额外寄存器轮询，报告已列入导出账本。逐决策保留消费报告集合、时钟界和执行时间，独立检查器重算因果性、队列和所有可行指派的原目标最优性。

离线年龄另使用发送前及恢复业务接收时间收紧区间，依赖生成早于恢复业务接收、同宿主单调计时及单节点恒定偏移。全部 sink 实际记录，包括之后丢失的报告，只用于离线时钟标定。监测状态仍只来自已收到的报告，离线区间不反馈给先前决策。窗口后报告保存但不提前用于测量，不宣称精确年龄或硬件同步。

## 导出链路与费用

损伤只作用在 sink 到 collector 的独立接口。三环境为无附加延迟、`delay 100ms 40ms`、`loss random 15%`。未加载分布表，当前内核的默认均匀抖动见 [WSL 内核 tabledist](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-6.6.87.2/net/sched/sch_netem.c#L319)。端到端延迟还包含软件处理。

本机 tc 6.1 不支持指定 seed，清单将 netem 种子记为 null，保留实际报告、缺失标识及 qdisc 计数。不同方法的随机损伤未配对，不能由单次年龄差异给方法排名。qdisc 处理背景帧，其总 drops 不全部归为遥测丢失。

sink 输出 PCAP 位于 qdisc 之前，记录导出尝试。账本按每次尝试计费，丢失报告仍计费，实际收到的帧及字节另保存。输出尝试不能当作损伤后已成功传过链路的字节。导出帧按最低 60 B Ethernet 长度计，不含 FCS、前导码或物理间隔。

| 容量 | 每链路带内附加字节 | sink 导出尝试 | 导出填充费用 | 全部数据 byte-hop |
| --- | --- | ---: | ---: | ---: |
| K=1 | 512、512、1280 B | 80 份 | 4800 B | 7104 |
| K=2 | 512、512、1472 B | 64 份 | 3904 B | 6400 |

四种方法及三环境保持同容量的相同选择和数据账本。损伤发生在选择之后，实际收报与年龄可以不同。

每案例正常控制为 64 次安装与 3 次启用。首案例冷安装 8338 B，容量启用 357 B，共 8695 B。后续复用表项的安装为 8077 B，共 8434 B，不能把首次与复用差异归为方法优势。前轮模型冷账本为 9315 B，含五次重复阈值预装。本入口已经设置阈值 1，省去重复写入，冷成本差额为 620 B。这是部署入口差异，没有追改原模型或声称优化贡献。

普通版全程 loopback Ethernet 捕获为 1100827 B，诊断版 4358917 B，分别含 24、6 个案例的路由 CLI、握手、TCP、诊断及 GSO 影响，不直接比较总量。API 序列化不再叠加到帧字节，整次捕获不按时间归因到单次 RPC。诊断重置及读取独立计量。

## 结果与复现

普通版八个正常收报配置及诊断版两个正常配置均在年龄上界下履约全部任务。延迟与丢失场景出现违约，转发、插入、收到的完整记录、导出尝试、费用及在线决策仍通过。普通版丢失 106 份，诊断版 19 份，全部保留。实现正确性通过不等于任务履约。

目前只有一条源端轨迹，没有统计重复、吞吐测量、异常检测或硬预算接纳保证。图显示共同 TT 配置的代表检查，CSV 包含全部 30 个案例及三个任务。四种方法在线分配不变，尚无新增动态收益证据。

从项目根目录运行，输出路径须不存在。网络运行需要 root，核对及绘图不需要 root。隔离构建方法见[时钟说明](README-clock-budget.md)。

```bash
python3 -m unittest discover -s tests -q
sudo python3 experiments/run_assignment_validation.py --ordinary \
  --output data/assignment-validation/new-ordinary
sudo python3 experiments/run_assignment_validation.py --families tt \
  --output data/assignment-validation/new-diagnostic
python3 experiments/analyze_assignment_validation.py \
  --run data/assignment-validation/new-ordinary \
  --output data/assignment-validation/new-audit-ordinary
python3 experiments/analyze_assignment_validation.py \
  --run data/assignment-validation/new-diagnostic \
  --output data/assignment-validation/new-audit-diagnostic
python3 experiments/plot_assignment_validation.py \
  --ordinary data/assignment-validation/new-audit-ordinary \
  --diagnostic data/assignment-validation/new-audit-diagnostic \
  --output figures/assignment-validation/new-figure
```

清单保存实际源码、P4 JSON 与隔离二进制摘要、软件版本、输入摘要、源端种子、状态、时间及开始结束负载。阶段资源快照见 `data/validation-host/assignment-01.json`、`assignment-02.json`。归档源码优先于当前同名脚本。

证据为[普通版原始清单](../data/assignment-validation/received-ordinary-02/manifest.json)、[普通版核对](../data/assignment-validation/audit-ordinary-02/summary.json)、[诊断版原始清单](../data/assignment-validation/received-diagnostic-01/manifest.json)、[诊断版核对](../data/assignment-validation/audit-diagnostic-01/summary.json)。图提供 [PNG](../figures/assignment-validation/received-01/received-age-and-export-cost.png)、[PDF](../figures/assignment-validation/received-01/received-age-and-export-cost.pdf)及[统计 CSV](../figures/assignment-validation/received-01/measurements.csv)。Times New Roman、300 dpi 和 PDF TrueType 嵌入已核对。

`received-ordinary-01` 保留第一次失败运行。四个正常配置完成后，显式 `distribution uniform` 因缺少 `uniform.dist` 而在延迟案例业务发送前失败。改用默认抖动后在新目录完整运行，旧 failed 状态和源码不计入通过数或额外统计重复。
