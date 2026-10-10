# 因果机会预测与剩余窗口预算诊断

本轮在首个闭环事件模型上增加时序机会代理、剩余窗口费用估计和动作门槛。主运行是 [n2b-01](../data/adaptive-reference/n2b-01/manifest.json)，最终分析是 [n2b-analysis-02](../data/adaptive-reference/n2b-analysis-02/manifest.json)。全部结果来自合成事件执行，尚无新版 P4 或 BMv2 动态闭环性能测量。

## 方法与信息边界

固定业务路径为 1→3 和 2→3，共有四个流—节点配置键。每个键选择禁用或 50/200/1000 ms 阈值，所有方法使用 K=1、12 B 身份头和 12 B 状态记录。成功插入才更新门控，禁用、修改阈值和重新启用均保留最后成功时间。全部禁用仍承担基础头。

| 方法 | 机会代理与选择 |
| --- | --- |
| static | 两个训练种子穷举 256 个配置，冻结静态 TT-INT |
| feedback | 原均匀机会与计数 EWMA，统一阈值反馈 |
| local | 原均匀机会，局部收益忽略容量，费用遵守 K |
| joint | 原均匀机会，联合评分包含容量 |
| phase_joint | 新时序机会，联合评分，名义预算 |
| uniform_guard | 原均匀机会，加入剩余窗口估计、门槛和通知后等待 |
| phase_guard | 新时序机会与剩余窗口估计，联合评分 |
| phase_local_guard | 新时序机会与剩余窗口估计，局部收益忽略容量 |

新机会代理使用已返回的付费计数差分及最近 3 s 的合法报告。去除重复和同节点旧记录后，用接收时刻减名义延迟得到相位代理。观测到至少三个重复群、足够小的周期偏差和群内间隔时外推突发，其余情况以计数速率和报告相位外推单包机会。隐藏包的位置和数量仍依赖假设。新代理同时改变速率平滑与机会时序，不能将这一消融解释为只改变相位。

算法不接收场景名称、未来业务、丢报标签、真实生成时间或执行器真值。名义包长、跳延迟和报告延迟属于公开先验。报告中的同节点时间只用于串行顺序过滤和既有门控估计，没有直接相减跨设备时间。真实生成年龄仅用于离线评价。

剩余窗口允许速率按“窗口总预算减已花费估计，再除以剩余时间”计算。基础头估计来自计数锚点和未观察尾段，元数据与导出取已收报告费用和过去自身预测的较大值，控制费用来自已知调用。通知后等待各流两个通知之后发出的计数请求返回。修复预测超限可以越过动作门槛。

字段 received_metadata_lower_bytes 和 received_export_lower_bytes 仅表达已收到、去重报告集合的费用。报告接收窗口与实际插入或导出尝试窗口不一致，可能计入延迟到达的预热报告。因此这些字段不是同一测量窗口实际费用的严格下界。整个已花费估计也不是费用真值或严格上界。

修复器支持有限窗口内的信用与欠账，不能保证未知业务下的硬预算。它没有逐秒强制监管，平均预算达标仍可能出现单秒超限。通知后等待、费用估计与动作门槛一起构成修复版本，本轮没有分别证明三者的独立收益。

## 训练、校准与费用

9 个场景包含稳定、规则突发、共享对象重点监测、流量与需求切换、稀疏停流、导出延迟与丢失、基础头预算不足，以及新增的突发抖动和无通知流量切换。每场景两档预算，每次 10 s，测量 [2,10) s，前 2 s 正常执行，缓存没有免费初始化。

| 阶段 | 种子与数量 |
| --- | --- |
| 静态训练 | 11/29，9216 个配置与种子执行 |
| 修复参数校准 | 37/53，三个方法各六组合，共 648 次执行 |
| 主保留执行 | 503/607/709/809，八种方法，共 576 次执行 |

六个校准组合为动作门槛 0/0.05/0.15 与预留比例 0/0.1 的笛卡尔积。各修复方法拥有相同的校准次数，跨所有场景与预算依次按实际超限次数、超限量、平均损失、更新调用和参数编码选择。生成主保留输入前写入冻结文件。最终 uniform_guard、phase_guard 选择门槛 0.05，phase_local_guard 选择 0.15，预留比例均为 0。局部与联合版本的冻结门槛不同，结果不能完全归因于容量评分。

上一轮的 101/211/307/401 已用于诊断，本轮未再用作主保留种子。503/607/709/809 在本轮分析后也已消费，后续需要重新划分测试输入。

评价沿用加权连续 min(生成 AoI/目标间隔,4)，未知状态取 4，共享对象不重复计收益。费用分列逐链路 IPv4 遥测增量、Ethernet 导出尝试和序列化控制 API 字节。丢失导出仍计费，API 字节不与帧字节相加。阈值写请求/回复为 94/30 B，许可写为 89/30 B，计数读为 81/40 B，共同控制预算为 1200 B/s。前两项核对历史网络写入记录，读取为本机绑定序列化参照，管理通知 128 B 是模型消息假设。本轮没有新增实际控制传输费用测量。

## 结果及限制

最终 194 项单元检查通过。独立检查器不导入策略或事件引擎，核对全部保留与校准原始事件。主保留部分核对 83840 个包执行实例、5760 次决策、252284 项费用，校准部分另核对 94320 个包、6480 次决策和 288987 项费用。新增 9360 次决策来源检查验证相位锚点、计数、自身预测历史及剩余预算计算。运行时回归快照为 192 项，最终分析另保存新增两项检查器破坏测试后的 194 项记录。

576 次主执行中 523 次满足总测量窗口预算。每种方法共 72 次，static、feedback、local、joint、phase_joint、uniform_guard、phase_guard、phase_local_guard 分别有 68、61、62、65、64、67、68、68 次达标。低档 budget_floor 的四个种子对所有方法均不可行，失败点完整保留。

规则突发的新近度预测平均绝对误差从 joint 的约 0.39355 降到 phase_guard 的 0.00467，但实际损失只恢复到静态 TT-INT 水平，并增加 242 B/s 控制轮询。无通知切换低预算中，joint 仅 1/4 种子平均预算达标，phase_guard 为 4/4，其损失约 1.37457，仍高于静态的 0.93917。抖动低预算也未优于静态。改进预测、增加可行性和提高监测质量是不同结论。

在原三档网格中，稳定及重点监测高预算的平均损失有所下降，同时增加导出或控制成本。所有种子的原始费用和单秒超限均保留。四个种子只支持有限诊断，配对 bootstrap 区间不作为统计显著性证明。

## 静态密网格事后挑战

看到主结果后，针对 stable/shared_priority 的高预算增加独立的静态网格核对，记录为 [n2b-density-01](../data/adaptive-reference/n2b-density-01/manifest.json)。阈值为 50/100/150/200/400/600/800/1000 ms，加禁用共 6561 个配置，两个训练种子合计 26244 次训练。配置冻结后复用四个已经消费的主输入，共 8 次验证。未重新选择主控制器参数。

| 场景，高预算 | 三档静态损失 | 密网格静态损失 | phase_guard 损失 |
| --- | ---: | ---: | ---: |
| stable | 0.608174 | 0.585636 | 0.576207 |
| shared_priority | 0.710219 | 0.628497 | 0.653406 |

密网格静态两组均 4/4 预算达标，平均导出 840 B/s，控制为 0。phase_guard 两组分别导出 836.25/864.375 B/s，控制为 249.75/273 B/s。重点监测的密网格静态平均损失更低，稳定场景中的差距明显缩小。不能将三档网格下的下降直接归因于编排收益。

这是事后、两个子集、已消费种子、配置空间不对称的挑战，不能作为共同密网格的公平盲测或一般静态最优结论。后续性能比较须统一配置网格和预算，再评价各项实际成本。

## 复现与数据

环境需要 Python 3、本机 BMv2 Standard/Thrift 绑定、Matplotlib、NumPy、Times New Roman 和 pdffonts。费用核对依赖已公开的历史写入记录。输出目录必须不存在，脚本保留执行源码、输入、事件、候选评分、校准结果、费用、状态和 SHA-256。

~~~bash
python3 -m unittest discover -s tests -v
python3 experiments/run_adaptive_reference.py \
  --output data/adaptive-reference/新的运行目录
python3 experiments/check_adaptive_reference.py \
  --run data/adaptive-reference/新的运行目录
python3 experiments/run_density_diagnostic.py \
  --primary data/adaptive-reference/新的运行目录 \
  --output data/adaptive-reference/新的密网格目录
python3 experiments/analyze_adaptive_reference.py \
  --run data/adaptive-reference/新的运行目录 \
  --density data/adaptive-reference/新的密网格目录 \
  --output data/adaptive-reference/新的分析目录
python3 experiments/plot_adaptive_reference.py \
  --run data/adaptive-reference/新的运行目录 \
  --density data/adaptive-reference/新的密网格目录 \
  --output figures/adaptive-reference/新的绘图目录
~~~

- [完整汇总 CSV](../data/adaptive-reference/n2b-analysis-02/aggregate.csv)、[配对差值](../data/adaptive-reference/n2b-analysis-02/paired-comparisons.json)及[预测误差](../data/adaptive-reference/n2b-analysis-02/forecast-errors.json)
- 损失与实际预算：[PNG](../figures/adaptive-reference/n2b-01/adaptive-loss-and-budget.png)、[PDF](../figures/adaptive-reference/n2b-01/adaptive-loss-and-budget.pdf)
- 机会与修复消融：[PNG](../figures/adaptive-reference/n2b-01/adaptive-ablations.png)、[PDF](../figures/adaptive-reference/n2b-01/adaptive-ablations.pdf)
- 静态密网格挑战：[PNG](../figures/adaptive-reference/n2b-01/static-density-challenge.png)、[PDF](../figures/adaptive-reference/n2b-01/static-density-challenge.pdf)

图片使用真正的 Times New Roman，PNG 为 300 dpi，PDF 嵌入 TrueType。误差线为四个种子的观察范围，叉号表示至少一个种子总窗口实际超预算。模型执行、独立来源检查、最终回归与绘图分别保存源码版本。
