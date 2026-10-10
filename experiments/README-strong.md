# 固定路径筛选、反馈、有限容量和格式对照

本组在三采样节点和独立 sink 的真实 BMv2/Mininet 链路上验证文献机制适配。新增 `strong_int.p4`、采集器和控制端独立保存，没有修改此前参与测量的核心或脚本。测试目的为正确性与成本口径检查，没有实现新的在线编排优化器。

## 机制及适配范围

| 机制 | 当前实现 | 与原工作范围的差别 |
| --- | --- | --- |
| INTDirector Dev | 对已登记流计 Cnt，逐到包累加 `max(IPG-Iopt,0)`，周期读取增量 | 已知精确流表，未实现 CM-sketch 发现、路径追踪和完整存续验证 |
| 速率与偏差筛选 | 连续两个观察窗口失败后移除，三个检查窗口后允许合格流采样 | 本组窗口为 400 ms，阈值为明确构造参数，未采用原文完整 10 s 发现过程 |
| 采样裕量 | `S=2/Iopt`，`N=max(1,floor(rate/S))`，仅 Cnt 能被 N 整除的包封装 | 流的激活使用本链上的合格候选，未实现通用选流优化和 PEN 路径扩展 |
| 均匀蓄水池 | 第 j 个合格节点以 1/j 概率替换，K=1 | 支持有容量与时间门控的节点流，未扩展到任意 K |
| 加权蓄水池 | 新节点权重 w，累计权重 W，以 w/W 概率替换 | 使用整数流式 K=1 选择，未实现实数优先级的通用加权蓄水池或动态队列权重 |
| INT-label 布尔反馈 | 报告长期未更新时置 force=1，持续陈旧时延长租期，恢复后租期到期清零 | 保留 Base A 和原型布尔反馈，未加入 Base B、Redis 或原始报文格式 |

Dev、检查次数及 α=2 的依据为 [INTDirector 正文](https://doi.org/10.1016/j.comnet.2026.112551)第 4.2、4.3 和 5.1 节。有限容量讨论见第 7.5 节。布尔反馈依据 [INT-label 作者公开正文](https://ng-95.github.io/files/INT_label_TPDS.pdf)第 5 节，与第 3 节整数级调整算法区别记录，不将适配执行器称为原论文完整复现。

本轮固定 `Iopt=100 ms`。源端只在收到首包后建立到包统计的有效状态，该包没有前一个可观测间隔，Dev 从 0 起计。这是已知流的测试约定，与原文表项生成时初始化 last_timestamp 有区别。Cnt、Dev 和最后到包时间独立于成功插入门控，关闭采样仍继续统计。Dev 尾部的无到包时间不会自行累加，低活跃检测还需 Cnt 窗口。

观察使用顺序 Thrift 读取，不宣称 Cnt 和 Dev 是原子快照。真实调用时间、读取值和窗口长度都保留。源端 N 用 identity hash 的模运算实现 Cnt%N，不因更新 N 而重置 Cnt。即便均值接近 40 pps，实测窗口差别也可能使 N 在 1、2 之间变化，不能把固定 N=2 当作所有运行的事实。

均匀和加权模式只支持 K=1，节点权重为 1 至 31，八节点以内的累计权重不超过 255。合格候选先受许可、门控、容量及 MTU 约束。替换不增加项数，不撤销上游曾成功插入的状态，上游已经携带记录的链路字节仍计费。当前加权值为 [3,2,1]，全部节点合格时理论终态比例为 [1/2,1/3,1/6]。这不是新的采样算法，也不是通用 K 的证明。

v1model random 的实际抽签逐包保留。本轮不能配置 BMv2 内部 RNG 种子，清单明确记为 null，工作负载种子为 0。独立核对可以重放实际抽签，不承诺重新运行得到相同随机统计。

## 格式与必要字段

四种格式使用 EtherType `0x88B6`、单独版本号，保证既有版本 1 的采集器与数据不受影响。

| 格式 | 版本 | 基础头 / 记录 | 适用范围 |
| --- | ---: | --- | --- |
| common | 2 | 8 / 20 B | 共同头，仍含未用选择键 |
| compact | 3 | 8 / 12 B | 只删除每记录的 8 B 预留选择键 |
| task | 4 | 5 / 12 B | Count、InitTTL、Version、Metric、累计候选权重，省去许可和配置版本 |
| prefix | 5 | 4 / 12 B | 再省去累计候选权重，只支持前缀插入 |

每条 12 B 记录保留 SwitchID、HopNum、6 B 生成时间和 4 B 出队队列深度，任务内容一致。task/prefix 省略的许可字段只在固定任务映射且无需源端版本更新的本组适配中成立。当前记录仍不包含完整生产流关联、独立端口和队列类别。它们是任务所需的精简适配格式，不能称为已经复现所有原论文的原生报文，也不能与仅含路径信息的 2 B 核心记录等价比较。

## 运行与复现

版本为 p4c 1.2.5.10、BMv2 1.15.0-2bdd0b7b、Mininet 2.3.0、Python 3.12.3。Thrift Python 绑定从 BMv2 CLI 安装包装脚本指向的目录读取，保持 TCP 连接，不反复加载 JSON。接口依据 [BMv2 标准 Thrift 定义](https://github.com/p4lang/behavioral-model/blob/main/thrift_src/standard.thrift)。不需要改动系统安装。

使用新目录运行，原始目录和派生目录均禁止覆盖。下面以 common 为例，其他格式替换 `--format` 与目录名。

```bash
python3 experiments/build_strong.py --format common
python3 experiments/build_strong.py --format task --ordinary
python3 -m unittest discover -s tests -v
python3 experiments/record_strong_host.py \
  --output data/strong-validation/host-new.json

sudo python3 experiments/run_strong_validation.py --format common \
  --output data/validation-runs/strong-common-new
sudo python3 experiments/run_strong_validation.py --format task --ordinary \
  --output data/validation-runs/strong-task-ordinary-new
python3 experiments/analyze_strong.py \
  --run data/validation-runs/strong-common-new \
  --output data/strong-validation/strong-common-new
```

诊断版 common、compact、task 各运行 10 个场景，prefix 运行 6 个，普通 task 再运行 10 个。可用 `--cases ratio mtu` 做指定检查。每个原始目录保留实际执行源码副本、SHA-256、版本、参数、时长、开始与结束负载以及状态。脚本只清理自己创建的网络和进程。

本轮资源快照见 [host-environment.json](../data/strong-validation/host-environment.json)，包含 CPU 型号、16 个逻辑 CPU、内存及 WSL2 内核，不包含主机名。逐次开始与结束负载另列在摘要中。该快照不是连续 CPU 性能剖析。

## 检查场景与实际证据

shared 使用两流各 5 pps、相差 5 ms、K=1，三节点阈值 100/300 ms/1 μs。ratio 检查 12 包按 1:3 产生 4 个报告。Dev 系列使用两条平均约 40 pps 的流，一条每 25 ms 一包，另一条每 200 ms 短突发 8 包。预设最低速率 30 pps、每窗口 Dev 上限 60 ms，合格后再结合均匀或加权选择。

feedback 系列每 20 ms 一包，K=3、阈值 100 ms，以 sink 丢弃导出克隆模拟一段约 400 ms 的导出丢失，业务原包完整转发。控制端每约 10 ms 检查收到的报告，以 k=2 维护布尔租期。注入的不是业务路径丢包，也没有随机丢包模型。

mtu 按各格式自身宽度构造完整记录能否插入和基础头能否添加的边界。uniform/weighted 各 768 包、2 ms 间隔、K=1，检查每个实际抽签和替换是否符合规则，不把一次观测分布当作统计性能结论。

| 原始运行及公开摘要 | 检查数 | 输入包数 | 核查输出 IPv4 帧 |
| --- | ---: | ---: | ---: |
| [strong-common-03](../data/strong-validation/strong-common-03/summary.json) | 10 | 2274 | 10795 |
| [strong-compact-01](../data/strong-validation/strong-compact-01/summary.json) | 10 | 2274 | 10799 |
| [strong-task-01](../data/strong-validation/strong-task-01/summary.json) | 10 | 2274 | 10781 |
| [strong-prefix-01](../data/strong-validation/strong-prefix-01/summary.json) | 6 | 354 | 1520 |
| [strong-task-ordinary-01](../data/strong-validation/strong-task-ordinary-01/summary.json) | 10 | 2274 | 10809 |
| 合计 | 46 | 9450 | 44704 |

全部网络检查通过，所有业务包传输内容、恢复长度、校验和、TTL=60 和真实 UDP 应用接收一致。诊断版另独立核对 Cnt/Dev、源端取样、逐节点决策、实际记录、最后成功状态与链路字节。普通版执行同一控制闭环并核对导出、业务与容量，不具有逐包诊断重放证据。全部单元检查现为 36 项。

共同格式单次均匀报告计数为 [245,246,277]，加权为 [401,249,118]。反馈 Base A 收到各节点 7 次更新，布尔反馈收到各 16 次，共同格式成本分别为 3516 和 6432 byte-hop。流量结束后再次陈旧会重新开启反馈，但不会在没有业务包时生成遥测。因此“强制采样”不是主动探测，也不能仅用报告数增加证明等预算收益。

## 时间与成本口径

本组年龄采用同一 Linux 宿主 CLOCK_MONOTONIC：发送前时刻作为真实快照生成事件的下界，采集 recvmsg 完成时刻作为上界及报告可用时刻。因果区间包含完整传输和用户态调度延迟，跨度约数毫秒，不表示硬件同步精度。P4 本地时间仍用于其实际门控，跨设备年龄不直接相减这些时间戳。

另核对本地时间能否通过恒定偏移映射到上述因果边界。compact 的 `dev_filter` 源端到包时间与普通 task 的 `feedback_base` 三节点生成时间不相容，摘要保留 `clock_diagnostics` 标记。功能核对仍说明程序按实际时间戳执行，因果年龄也继续有效，但这两项不能解释为稳定本地时钟下的正常时间行为。该检查只说明已有观察的相容性，不能证明其他时刻持续稳定。性能比较前还需固定 BMv2 的计时实现，单调域收报不等于已经修复交换机时钟。

shared 从第 9 个包开始评价，保留此前实际已经收到的更新。五次运行均满足 250/450/650 ms 三个任务，违约上下界均为 0。feedback 从第一个包起评价冷启动和导出丢失，未知状态计入违约，两种方法都不满足全窗硬期限，不能因丢失结束后恢复就忽略初始缺口。其他场景当前只评价功能和成本，没有给出年龄性能排名。

带内按每条采样链路的 IPv4 附加字节计，导出按捕获的完整 Ethernet 帧计。按唯一包 ID 关联链路，避免依赖 wall clock 的窗口匹配。恢复后的目的端元数据为 0，所有 IPv4 帧 MTU 不超过 1500 B。

| 相同 [12,6,6] 分配的 shared 格式 | 捕获 byte-hop | 补齐最小导出帧后的推导 byte-hop |
| --- | ---: | ---: |
| common | 3144 | 3144 |
| compact | 2520 | 2664 |
| task | 2232 | 2448 |
| prefix | 2136 | 2376 |

捕获中的 compact/task/prefix 单项报告分别为 54/51/50 B，veth 没有自动补齐。分析另按导出帧至少 60 B、不含 FCS 的口径计算补齐值，依据 [Linux Ethernet 定义](https://github.com/torvalds/linux/blob/master/include/uapi/linux/if_ether.h)。业务帧均已大于 60 B，带内增量不受该修正影响。两种口径都不含 FCS、前导码和物理间隔，补齐列是明确前提的推导，不是硬件测量或额外实际抓到的字节。

本机 RPC/TCP 仍独立计费，普通 task 为 948522 B，common 诊断为 14362052 B，主要差异含批量诊断读回。各次 socket 丢包计数为 0。loopback/GSO、一次静态初始化和验证读取均在捕获中，不能将总量当作实际部署链路开销或线上观察预算。公开只保留长度、时间、端口和 API 操作，不含 RPC 载荷。

## 图和记录保留

四种诊断派生数据齐备后，绘制正式图：

```bash
python3 experiments/plot_strong.py \
  --runs data/strong-validation/strong-common-03 \
         data/strong-validation/strong-compact-01 \
         data/strong-validation/strong-task-01 \
         data/strong-validation/strong-prefix-01 \
  --output figures/strong-new
```

图为同源 300 dpi PNG 与嵌入 Times New Roman TrueType 的 PDF。格式成本图只在记录分配一致时生成。蓄水池图的黑色短横线表示理论边际，不是置信区间。反馈图给出配置的导出丢失区间和实际控制调用完成时间，不代表所有重复运行都有相同响应延迟。

初次 `strong-common-01` 因错误要求空闲末尾 force 必须为 0 而记录失败，实际反馈已经开启并退出，随后因流量结束再次陈旧而开启。修正检查器后保留旧运行和执行源码，`strong-common-02` 另通过 8 个场景。最终 `strong-common-03` 增加两项 Dev 与蓄水池组合，完整通过 10 项。此前微型检查、补齐最小帧长前的派生产物及绘图版本均保留在本地原始目录，没有重写失败清单。

这些构造验证没有通用 CM-sketch 流发现、PEN 路径扩展、INT-label Base B、随机未来的硬期限保证或统计性能结论。独立离线模型目前仍采用共同 8+20 B 默认格式，正式同预算求解须先扩展其格式和观测成本接口，不能混用本组精简格式的字节。
