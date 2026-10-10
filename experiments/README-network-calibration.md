# 实际可用性与分块网络校准

本阶段复用普通版本 6 P4、真实字段确认和自有控制账本，补齐文件前缀的可用性记录。策略只使用当时已交付的信息。相比前一轮，主循环在文件读取、解析和状态更新完成后确定实际截止，保留消息 0/5 s 释放、整数秒轮询、共同网格、K=1、48 候选和六写限额。策略参数及初始静态配置保持原模型阶段冻结值。

业务为已消费训练种子 29，四配置为 stable/high、shared_priority/high、switch/low、bursty_jitter/low，全部八方法。每单元三个完整运行块，共 96 案例。每例 10 s，2 s 预热，测量 [2,10) s。每块包含全部 32 单元，排序种子 27182828、31415926、16180339，仅改变执行顺序，不增加业务种子。三个块用于观察软件部署波动，不能称独立业务测试或统计充分样本量。开发主矩阵期间同宿主完成了两次烟测离线分析及各约 5 s 单元回归，没有第二组网络并行。低平均负载不排除调度干扰，正式比较需要固定宿主执行条件。

## 信息什么时候可用

`collector.jsonl` 的 `receive_monotonic_ns` 是采集程序 recvmsg 完成后的主机单调取时，另保存内核墙钟抓包时间。它不是线路上精确物理到达时间。`availability.jsonl` 保存控制器实际读取的文件快照长度、完整前缀长度、摘要、行数、读取/解析开始和结束，以及状态更新开始和结束。最终追加文件可以重建每个实际字节前缀。

状态更新开始/结束包围本批信息进入状态的时间。实际截止和求解均在更新结束之后。已收到但未出现在这次读取前缀内的报告，不会被事后补入当时的视图。独立检查器从前缀逐行重建完整合法报告与共享缓存，核对每次决策全部来源。重复、旧报告和未知身份处理沿用已验收机制。

UNIX 通知保留 sendto 开始/结束和 recv 开始/结束，以及首次包含该版本的决策截止。不同线程的 sendto 完成取时可能晚于 recv 完成取时，不据此伪造串行时序或负队列时延。通知有效负载与 API 字节分层，长度以实际接收为准。静态不执行无用途源读和配置写。

## 两处年龄与案例波动

`summary.csv` 的损失界保留采集端实收 AoI 口径，`controller-visible-bounds.json` 和 `case-availability.csv` 另给出控制器视图 AoI 条件界。前者使用采集程序的实际收报，后者使用实际进入状态的时间区间。两者使用相同生成时间因果界和完整活跃任务分母，不把收到和进入策略状态混称。

`report-availability.csv` 记录合法新报告从采集程序接收到进入状态的观察上界，`notice-availability.csv` 记录通知可用时刻，`availability-batches.csv` 分列读取和更新耗时。`block-aggregate.csv` 的独立重复分母为三个完整案例，保留所有费用达标/失败和条件损失区间，报告分布仅作描述。均值区间不是置信区间，不从包数推导统计显著性。

带内、导出尝试、API 和本地消息继续分别按实际窗口核对，PCAP 墙钟与单调时钟的恒定偏移仍是条件假设。交集兼容不证明未观察时段没有跳变，完成窗口达标不构成硬监管。主矩阵为普通 P4，不带内部 gate trace，不据此重新宣称全部内部门控逐事件正确性。

## 运行与独立复核

环境及普通 P4 准备见 [版本 6 说明](README-into-validation.md)，共同网格接口见 [网络试点说明](README-matched-network.md)。输出目录必须使用新编号。两个 smoke 案例在主矩阵之外，不纳入三块汇总。

```bash
sudo python3 experiments/run_network_calibration.py --output data/network-entry-runs/<new-run-id>
python3 experiments/check_network_calibration.py --run data/network-entry-runs/<new-run-id>
python3 experiments/analyze_network_calibration.py --run data/network-entry-runs/<new-run-id> --output data/network-entry-validation/<new-analysis-id>
python3 experiments/check_calibration_analysis.py --analysis data/network-entry-validation/<new-analysis-id>
python3 experiments/plot_network_calibration.py --analysis data/network-entry-validation/<new-analysis-id> --output figures/network-calibration/<new-figure-id>
```

保留数据可以直接复核：

```bash
python3 experiments/check_calibration_analysis.py --analysis data/network-entry-validation/n3d-blocks-01
```

工程来源或业务破坏保留失败并停止运行，预算失败和可评价性差异保留，不选择性删除。旧运行器和源码快照保持独立。编译 JSON 的本机源路径仅在原始目录保存，公开清单保留摘要和 P4 源码。

## 后续有限静态挑战

[预声明候选](../data/network-static-plan/n3e-candidates-01/candidate-plan.json) 每配置单元 24 个，由旧模型训练 11/29 的完整 6561 网格评分产生。包含全禁用、全 50 ms、模型排序、邻近配置及统一网格随机候选，保留去重与选入理由。当前状态为 `prepared`，没有新的网络评分。

```bash
python3 experiments/prepare_network_static.py --reference data/matched-reference/n3b-matched-01 --output data/network-static-plan/<new-plan-id>
```

候选后续在 11/29 两个已消费训练业务上分别运行，预计 192 案例。实际费用上界在两业务都达标后，按最坏年龄损失上界选择。有限候选不称全网格网络最优或已经完成网络充分调参。正式比较还需动态校准、预算与未消费测试分组冻结。

## 保留运行结果

完整主矩阵 [n3d-blocks-01](../data/network-entry-validation/n3d-blocks-01/manifest.json) 的 96 案例全部工程通过，交付 15360 包，采集 10594 报告，恢复 56674 输出帧，960 次决策、457 次成功字段更新，没有跳过周期。运行与最终单元回归各 266 项通过。

93/96 条件窗口达标，三个块的 switch/low/local 均超过 e3 与导出预算，原始和汇总全部保留。报告接收至进入视图的观察上界最大约 979.72 ms，初始通知首周期可用为 10/96。三个案例之间的损失中点范围最大约 0.34545，不能据本轮作正式统计排名。

正式可用性图为 [PNG](../figures/network-calibration/n3d-blocks-02/network-view-availability.png) 和 [PDF](../figures/network-calibration/n3d-blocks-02/network-view-availability.pdf)。两处年龄图为 [PNG](../figures/network-calibration/n3d-blocks-02/network-age-observation-points.png) 和 [PDF](../figures/network-calibration/n3d-blocks-02/network-age-observation-points.pdf)。两图使用 Times New Roman，PNG 为 300 dpi，PDF 嵌入字体。年龄图红叉标记有窗口预算失败的单元。
