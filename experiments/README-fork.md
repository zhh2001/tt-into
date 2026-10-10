# 固定分支路径和需求变更验证

本阶段检查任务资格是否来自真实业务路径，以及原有 TT-INT 阈值更新能否执行预声明的需求变化。结果是单次确定性正确性验证，不用于估计随机性能、硬件性能或算法改善比例。

## 物理对象和流量

三个对象都是 `(SwitchID, egress port=2, queue class=0)` 的队列快照。F1 经过 S1、S3，F2 经过 S2、S3，F3 只经过 S3，随后均经过独立 sink S4。S3 上三条流观测的是同一出口队列，分支对象只允许经过该分支的流观测。采集端通过本实验固定拓扑解释 SwitchID，不把它推广为任意多出口或多队列的生产对象标识。

F1、F2 周期均为 100 ms，错开 50 ms。F3 周期为 200 ms，初始偏移 25 ms。一次检查发送 80 个 120 B IPv4/UDP 包，包含 800 ms 真实预热和 2400 ms 测量窗口。各检查重新建立门控状态并保留预热产生的样本，需求切换时不重置缓存或成功门控。

| 测量窗口相对时间 | S1 期限 | S2 期限 | S3 期限 |
| --- | --- | --- | --- |
| 0–1220 ms | 150 ms | 150 ms | 250 ms |
| 1220–2400 ms | 650 ms | 650 ms | 175 ms |

沿用独立 `strong_int.p4`，没有修改历史数据平面源码。此次使用 prefix 格式，固定头 4 B，记录 12 B，包含 SwitchID、HopNum、egress 生成时间和队列快照。没有许可掩码或策略 Epoch，本次顺序 Thrift 写入也不宣称跨交换机原子生效。

## 网络检查结果

| 配置 | 容量 | 第一阶段履约对象数 | 第二阶段履约对象数 | 全部包的逐链路附加字节 | 导出字节，补齐最小帧 |
| --- | --- | --- | --- | --- | --- |
| 固定逐流 TT-INT | 1 | 3/3 | 2/3 | 512, 512, 1280 | 4800 |
| 固定共享门控 | 1 | 3/3 | 2/3 | 512, 512, 1280 | 4800 |
| 固定折中阈值 150 ms | 1 | 1/3 | 3/3 | 320, 320, 1280 | 4800 |
| 预声明 TT-INT 阈值更新 | 1 | 3/3 | 3/3 | 404, 404, 1280 | 4800 |
| 固定逐流 TT-INT，容量放宽 | 2 | 3/3 | 3/3 | 512, 512, 2048 | 4928 |

前三行和更新的诊断检查见 [fork-diag-03](../data/fork-validation/fork-diag-03/summary.json)。关闭逐包诊断后的更新和容量放宽见 [fork-ordinary-02](../data/fork-validation/fork-ordinary-02/summary.json)。普通版首次更新检查也保留在 [fork-ordinary-01](../data/fork-validation/fork-ordinary-01/summary.json)。共通过 7 项检查，完整交付 560 个业务包，独立核查 2128 个输出 IPv4 帧。单元检查共 56 项。

履约按连续年龄违约时间积分，年龄区间来自同一 Linux 宿主的发送前与采集 `recvmsg` 完成边界。报告生成时间落在该区间内，跨进程的 P4 时间戳不直接用于年龄相减。表中履约采用年龄上界判断，窗口起点保留真实收报前史。报告生成时间另做恒偏移可相容性核对，全部报告节点满足条件，但这不证明硬件同步。

容量为 1 时，更新配置在测量时间 1200 ms 起允许分支更少插入，以提前准备 1220 ms 的新要求。实际下发时先暂停同一发送进程，确认所有更新前报告已取到，再分别写入 S1 流 1 和 S2 流 2 的 `time_threshold=300001 us`。后续业务包在两次 RPC 返回后才发送。发送进程保持套接字，不通过重启进程制造额外间隔。屏障只适用于本次受控正确性运行，需求提前声明，未实现未知流量和未知需求下的在线控制器。

普通版两次更新各为 94 B 请求和 30 B 回复，共 248 B 序列化载荷。它不是完整控制网络开销。普通版两个检查的全部控制捕获为 709545 B，包含就绪握手、初始 CLI、安装、更新、TCP 和 GSO。按四个 Thrift 服务器的请求和回复分成八个方向，不能按抓包完成时刻准确归因单次 RPC。序列化费用和 Ethernet 捕获费用不相加。

预算在运行前从 [配置文件](configs/fork-correctness-budget.json)保存并核对摘要。数据限制按每项检查计费，控制限制覆盖整次运行，包括初始化和诊断。所有检查均满足这个宽松正确性预算。K=2 固定配置同样满足预算，故当前预算不能被描述为必然迫使 K=1 的资源限制。尚未完成独立运行、训练与测试隔离后的等预算性能比较。

## 事件配置筛查

[事件结果](../data/path-reference/fork-03/results.json)包含 84375 次逐流固定配置、3375 次共享配置及 19000 次共享整数加权蓄水池配置/种子试验，共 106750 次。逐流索引为 `(S1,F1), (S2,F2), (S3,F1), (S3,F2), (S3,F3)`，各自枚举 1、100001、200001、300001 us 和容量 0 禁用。固定前缀同时枚举各流 1:N 的 N=1、2 及所有初始计数相位。蓄水池使用具备累计字段的 task 5+12 B 格式，19 种比例不同的整数权重、8 个 Python 参照种子、N=1。

这些有限配置/种子都没有同时履约六个对象阶段要求。报告的最佳结果在整条给定轨迹中选择，随机配置展示最好的单个种子，不是重复试验均值，也不是测试集结果。该筛查未覆盖完整 Dev 动态选流、布尔或整数反馈控制器、任意权重与任意阈值。Python 随机种子不能直接映射到 BMv2 的随机序列。

预声明阈值更新在事件参照中可履约全部要求，放宽到 K=2 后固定前缀也可全部履约。事件模型没有自行产生控制费用，部署及预算证据来自单独的真实网络运行。下游替换仍保留上游已发生的带内传播费用和成功门控，不用最终记录反推蓄水池的全路径开销。

## 复现

需要上一阶段的隔离单调时钟 BMv2 构建，默认使用 `build/bmv2-steady-01`，也可传 `--steady-build` 指定自身已构建并记录摘要的目录。全局 `simple_switch` 不被修改，新的运行目录必须不存在。

```bash
python3 -m unittest discover -s tests -v
python3 experiments/run_path_reference.py --output data/path-reference/新事件运行
sudo python3 experiments/run_fork_validation.py --output data/validation-runs/新诊断运行
sudo python3 experiments/run_fork_validation.py --ordinary --output data/validation-runs/新普通运行
python3 experiments/analyze_fork_validation.py \
  --run data/validation-runs/新诊断运行 --output data/fork-validation/新诊断运行
python3 experiments/analyze_fork_validation.py \
  --run data/validation-runs/新普通运行 --output data/fork-validation/新普通运行
python3 experiments/plot_fork_validation.py \
  --diagnostic data/fork-validation/新诊断运行 \
  --ordinary data/fork-validation/新普通运行 --output figures/新图目录
```

原始测量、运行源码快照及失败记录全部保留。初次运行在发包前因接口查找错误停止，后续动态运行因第二个发送进程启动过慢超过生效点而失败，两项都未作为成功样本计入。修复后重新运行，不覆盖失败记录。事件首次运行的权重枚举错误也保留，修复及新增检查后再生成当前结果。

公开数据保留输入、收发事件、业务与采集 PCAP、诊断记录、每条链路输出的字段摘要、控制帧计数和源码摘要。原始控制 RPC 载荷、命名空间进程日志等留在原始运行目录。

拓扑图提供 [drawio](../figures/fork-validation/fork-topology.drawio)、[PNG](../figures/fork-validation/fork-topology.png)、[PDF](../figures/fork-validation/fork-topology.pdf)。节点、连线、路径标注都可独立编辑。使用调整后的同格式图源重新导出时传入 `--drawio 路径`，脚本直接读取图源的几何信息。

共同出口年龄图提供 [PNG](../figures/fork-validation/shared-outlet-causal-age.png) 和 [PDF](../figures/fork-validation/shared-outlet-causal-age.pdf)。左侧使用诊断运行，后两侧使用普通运行，仅展示共同出口，其他对象的连续指标在摘要中。浅色带表示因果区间，曲线表示上界，虚线表示期限。K=2 面板是容量放宽检查，不能视为相同容量的性能比较。

两种格式均为同一绘图源输出，PNG 300 dpi，PDF 嵌入真正的 Times New Roman TrueType 字体。拓扑图由同名 drawio 图源读取几何信息导出。
