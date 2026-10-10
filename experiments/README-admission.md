# 固定分支轨迹的任务接纳核对

本验证沿用固定分支的 80 个业务包事件和两个需求阶段，不启动 BMv2。目标是核对硬期限接纳、容量和数据预算，结果仅适用于给定的未来已知轨迹。各数据集按实际执行版本保存源码快照，新运行不得覆盖已有目录。

## 输入及接口

对象、路径、120 B IPv4 包、800 ms 预热和 2400 ms 窗口沿用[固定分支说明](README-fork.md)。S1、S2、S3 的任务权重分别为 2、2、1。某任务在两个阶段都没有正时长的年龄违约才被接纳，总权重上限为 5，不是五个任务。

`trace_admission.solve` 使用 prefix 4+12 B 格式或调用方声明的其他格式。每个物理对象只能由经过它的包采样。收报可用时间、生成时间和 ingress 门控时间分别给定，丢失报告不能更新缓存。后到的旧报告不能回退最新生成时间。未知缓存计入年龄违约，初始已收样本必须显式提供。

MILP 先最大化整段轨迹都履约的任务权重，再在相同权重下最小化数据费用。自由选择每包记录属于离线上界，`deployment_feasibility_established` 始终为 false。静态阈值和初始成功门控可作为额外约束，默认零间隔用于放宽。`locked_history` 可以固定窗口前已经发生的记录选择，拒绝任务的历史样本及费用仍保留。

默认所有合法包固定封装，包括没有记录的包和预热包。带内费用按三条实际链路累计，导出补齐最小 60 B Ethernet 帧，不含 FCS。无替换的记录从采样位置传播到路径末端。导出后丢失仍收费，导出前未发送不收费。optional 封装另属允许省略源端头的放宽，不能直接当作固定封装的部署方案。

数据网格的分支预算各为 1024 B，共同链路为 640、800、960、1120、1280、1600、2048 B，导出为 1200、2400、3600、5000 B，容量为 1、2。共 56 点，全部保留，不按结果删选。成本覆盖整条轨迹，包括预热。

固定控制账本可单独核对安装、观察及更新等费用，超限返回 infeasible。该账本的时刻不会自动改变数据平面。网格没有加入控制账本，也没有测量新初始化的额外下发费用。序列化控制载荷与 Ethernet 数据字节不合成总量。

`certificates` 输出及时报告缺口、MTU 缺口和同时容量不足的必要条件证书。容量证书列出时刻、对象集合、合格包及可用槽数。没有发现这些冲突不能保证联合履约，跨时刻门控争用需要继续求解。

## 保存结果

| 数据集 | 内容 | 接纳上界与配置相同的资源点 |
| --- | --- | ---: |
| [fork-01](../data/admission-reference/fork-01/summary.json) | 56 点上界、11250 次双阶段 TT-INT 配置、六种约束诊断和固定控制超限 | 11/56 |
| [history-01](../data/admission-reference/history-01/summary.json) | 固定每点最佳候选自身预热的 56 次条件求解 | 56/56，在各自给定前史下 |
| [initialization-01](../data/admission-reference/initialization-01/summary.json) | 临近窗口启用和晚期阈值扩展，共 27450 次配置 | 两个配置族均为 45/56 |
| [flow-indices-01](../data/admission-reference/flow-indices-01/summary.json) | 晚期三个共享逐流索引分别调参，共 36864 次配置 | 51/56 |
| [flow-indices-02](../data/admission-reference/flow-indices-02/summary.json) | 允许早期搭载，但共享分支流在窗口起点才启用，共 331776 次配置 | 51/56 |
| [flow-indices-03](../data/admission-reference/flow-indices-03/summary.json) | 预热也允许原有 K=2 搭载，共 331776 次配置 | 56/56 |

所有正常配置尝试共 739116 次。125 次接纳接口求解包含网格、约束诊断和前史核对，不等于 MILP 内部优化调用次数。最终 56 个候选通过[独立核查](../data/admission-reference/audit-01/summary.json)，验证六个数据集的源文件及产物摘要，再用完整连续年龄积分重放最终候选。项目单元检查共 70 项，包含新增 14 项和 8 组随机轨迹的 32 个格式/封装穷举组合。

最终配置只使用原有逐流容量 0 禁用、非零阈值及容量 1、2，不增加报文字段。预热从禁用状态开始，在相对 -175001 us 启用共同出口流 3，-100001 us 启用分支 1 及共同出口流 1，-50001 us 启用分支 2 及共同出口流 2。0 us 进入早期配置，1200000 us 进入晚期配置，成功门控状态始终保留。

早期两分支阈值为 1 us 或禁用，共同出口流 1、2 分别为 1、100001 us 或禁用，流 3 为 1 us 或禁用，共 72 种。晚期两分支分别为 1、100001、200001、300001、500001 us 或禁用，共同出口每流分别为 1、100001、200001 us 或禁用，共 2304 种。每种容量核对 165888 个组合。容量搭载、资格启用和阈值调整均是既有机制的给定轨迹适配。

最终 56 点只有 25 点能接纳全部三个任务，其余受当前资源限制。配置达到接纳权重上界，不表示同时达到最小数据费用，最终候选仍比离线最小费用多 84–372 byte-hop。没有穷尽任意阈值、任意控制时刻、1:N 初始相位或文献完整控制器。新初始化尚未移植到网络验证，不据此声称控制开销或等预算性能改善。

后续两个逐流网格的候选用 `.jsonl.gz` 无损保存，解压后每行一条配置及成本结果。其他网格保留原 JSON/JSONL。运行状态、主机负载、软件版本、输入及源码摘要分别记录在各自 manifest。

## 复现

需要 Python、NumPy、SciPy 的 MILP 接口和 Matplotlib，无须 sudo。

```bash
python3 -m unittest discover -s tests -v
python3 experiments/run_admission_reference.py --output data/admission-reference/新网格
python3 experiments/check_admission_history.py \
  --run data/admission-reference/新网格 --output data/admission-reference/新前史
python3 experiments/check_admission_initialization.py \
  --run data/admission-reference/新网格 --output data/admission-reference/新初始化
python3 experiments/check_admission_flow_indices.py \
  --run data/admission-reference/新网格 --output data/admission-reference/新逐流网格
python3 experiments/check_admission_outputs.py \
  --base data/admission-reference/新网格 --tuned data/admission-reference/新逐流网格 \
  --runs data/admission-reference/新网格 data/admission-reference/新前史 \
         data/admission-reference/新初始化 data/admission-reference/新逐流网格 \
  --output data/admission-reference/新核查
python3 experiments/plot_admission_reference.py \
  --run data/admission-reference/新网格 --tuned data/admission-reference/新逐流网格 \
  --output figures/新接纳图
```

当前逐流脚本运行最终完整配置族，不重新制造早先受限配置。若需重放历史版本，应使用对应目录保存的实际运行源码。求解器可返回 limited，未证明最优时不能作为接纳上界。当前完整资源网格均证明最优，最小费用的第二次优化也分别记录状态。

对比图见 [PNG](../figures/admission-reference-comparison/fork-admission-budget-grid.png)和 [PDF](../figures/admission-reference-comparison/fork-admission-budget-grid.pdf)。上半图为最初受限配置，下半图为最终初始化与逐流调参。每格是“离线接纳上界 / TT-INT 配置接纳权重”，图中明确不含控制费用。图片来自同一脚本，PNG 为 300 dpi，PDF 嵌入实际 Times New Roman TrueType 字体。
