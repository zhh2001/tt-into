# 共同网格真实网络开发试点

本入口将共同网格策略接到独立版本 6 P4、计费 Thrift、合法报告缓存及逐字段确认状态。固定业务路径、共享出口队列对象、12 B 基础头、12 B 记录和 K=1。八档阈值为 50、100、150、200、400、600、800、1000 ms，另有禁用，共 6561 个四键配置。每次决策最多评价 48 个候选，最多发行六次配置写。

`data/network-entry-validation/n3c-pilot-01/` 保存八方法乘四开发配置的 32 个普通版 BMv2 案例。四配置为稳定高档、重点监测高档、有通知切换低档、带抖动突发低档。每例 10 s，前 2 s 预热，费用评价窗口为 [2,10) s。业务种子 11 已参与模型训练，各方法每配置仅运行一次，执行顺序未随机化。这是部署与测量检查，不能作为独立测试或统计性能排名。

## 入口与信息来源

`src/control/matched_live.py` 在真实循环中构造规划器和自有控制账本。策略只使用已取得报告、付费源计数、实际收到的任务通知、已确认配置以及自己的完成调用。源端发包记录、离线 PCAP、交换机真值和未来业务计划不进入在线决策。IPv4 ID 用于离线逐包关联，在线流身份使用 FlowTag 与 Session。

八方法为 `static`、`feedback`、`local`、`joint`、`phase_joint`、`uniform_guard`、`phase_guard`、`phase_local_guard`。初始配置与参数沿用共同网格事件阶段冻结的训练和校准结果。静态方法保持配置，不执行无用途的源计数轮询。它是模型充分调参配置的部署起点，尚未完成实际网络充分调参。

循环记录排程时刻、真实截止、求解开始与结束、同步 RPC 的发行与返回、配置确认及计数取得。真实耗时参与后续业务执行，未代入固定 100 us 回复。迟到一整周期时记录跳过，无追赶执行。字段未知或部分失败先付费核对，无法恢复明确配置时保留记录并停止案例。各字段顺序更新，无跨节点原子承诺。

控制账本将请求与回复分别归入实际时刻，完整长度仅在调用返回后交给策略。初始化保留，诊断读取与诊断 trace 清零从合法费用排除。通知以实际 UNIX 接收有效负载单独计费。API 字节、通知有效负载和捕获网络帧分别评价，不相加。整次运行的表安装与诊断捕获仍保存在原始控制记录中，不将其伪装为窗口在线费用。

## 本轮记录

32 案例全部通过，交付 5120 个业务包，采集 3486 个报告，独立恢复 18846 个输出帧，执行 320 次决策及 126 次成功配置更新，没有跳过周期。线格式、身份、转发、真实计数、配置确认和自有费用共核对 118085 项，合法共享缓存与预算来源另核对 1600 项。运行时单元回归为 251 项，最终分析补齐四项时钟和边界检查后为 255 项，两份日志均保留。

全部案例在条件窗口费用上界下达标。带内费用逐链路核对，禁用仍保留基础头，导出尝试从交换机输出抓包恢复。PCAP 墙钟与主机单调时钟采用配对取时的恒定偏移交集，并保留抓包微秒取整导致的窗口边界不确定性。上界全部满足才标记 `within`，下界已超限标记 `exceeds`，其余保留 `ambiguous` 或 `unavailable`。采样交集兼容不证明未采样时段不存在时钟跳变，也不构成在线硬预算保证。

生成年龄从发送与接收因果区间、同节点生成时间戳及微秒取整恢复条件上下界。不同交换机时间戳不直接相减，旧报告与重复报告不重置新样本缓存，全部活跃任务保留。主矩阵使用普通 P4，未新增内部逐包 gate trace，此处不能声称重新核对了全部内部门控事件。

`summary.csv` 保存每个案例的损失界、覆盖、费用及时间偏差，`timing.csv` 保存每次求解和真实周期调用。协调搜索方法的案例平均求解耗时约 23.5–35.4 ms，单次最大 69.18 ms。在线 RPC 的案例平均约 0.20–0.53 ms，单次最大 2.42 ms，区别于事件模型的 0.1 ms 先验。源发包最大排程迟到 17.48 ms，通知可见性迟到最大约 1000.74 ms。这些是软件环境观察，不能外推为硬件交换机性能。

## 复现与检查

网络运行需要项目隔离的 steady_clock BMv2 构建、p4c、Mininet 和 Python 依赖。先完成 [版本 6 正确性说明](README-into-validation.md) 中的环境准备。运行器检查二进制摘要、编译 P4，保存执行源码、软件版本及宿主负载。仅清理自己创建的网络与进程。输出目录必须为新的实验编号。

```bash
sudo python3 experiments/run_matched_network.py --output data/network-entry-runs/<new-run-id>
python3 experiments/check_matched_network.py --run data/network-entry-runs/<new-run-id>
python3 experiments/analyze_matched_network.py --run data/network-entry-runs/<new-run-id> --output data/network-entry-validation/<new-analysis-id>
python3 experiments/plot_matched_network.py --analysis data/network-entry-validation/<new-analysis-id> --output figures/network-entry/<new-figure-id>
```

保留数据可直接独立核对，无需重新启动交换机：

```bash
python3 experiments/check_matched_network.py --run data/network-entry-validation/n3c-pilot-01
```

实际调用、收发、决策、费用、源码及输出 PCAP 均在分析目录内。原始编译 JSON 含本机源文件路径，保留在原运行目录，交付清单保留其摘要和 P4 源码。分析目录不复制该编译 JSON。每个阶段的源码快照与产物摘要独立保存，不覆盖原始记录。

正式时间图为 [PNG](../figures/network-entry/n3c-pilot-01/matched-network-timing.png) 和 [PDF](../figures/network-entry/n3c-pilot-01/matched-network-timing.pdf)，均使用 Times New Roman。点为每案例观察均值，横线为该案例内观察范围，不是置信区间。后续性能比较需要重新冻结网络调参、校准、独立测试及执行顺序规则，并加入实际网络静态调参挑战。
