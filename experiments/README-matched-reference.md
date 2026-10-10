# 共同网格完整事件比较

静态与动态均使用禁用及 50/100/150/200/400/600/800/1000 ms 八档阈值，四个流—节点键共 6561 个配置。K=1、版本 6 的 12 B 基础头和 12 B 记录、固定路径 1→3 与 2→3 共同保持。动态搜索每周期最多评价 48 个候选，写入最多 6 个字段，不能把有限搜索称为完整网格最优。

结果为合成事件模型，保留逐包 ingress/egress、在途逐字段生效、计数快照、报告接收及完成费用。它不模拟真实排队服务，不提供硬件性能或 BMv2 网络性能结论。

## 执行与信息范围

八种方法为充分调参 static、统一 feedback、独立 local、joint，以及 phase_joint、uniform_guard、phase_guard 和 phase_local_guard。后三种修复方法分别校准六个动作门槛与预留组合，其余设置固定。各方法采用共同静态训练配置和相同合法管理路径，不向策略提供场景名、包未来位置、未收导出或生成时间真值。

静态在训练输入穷举全部网格，按最大超限、平均损失、启用数量及配置编码排序冻结；动态从已确认配置、实收报告、实际取得且计费的源计数和已收到需求消息决策。源计数按秒轮询，静态没有无用途轮询。动态的完整配置集合相同，搜索能力有限，另保存固定视图穷举诊断。

每次 10 s，前 2 s 为共同预热，损失与平均费用窗口为 [2,10) s。需求在 0/5 s 释放，经模型 1 us 传输后，在首个整数秒轮询，即 1/6 s 可用。任务生效时间保留原始阶段边界。所有方法承担同额消息费用，不因通知迟到删除任务评价区间。

API 字节长度引用 N3 正常实测：源计数读 81/40 B、阈值写 94/30 B、许可写 89/30 B。主矩阵为合成调用，写生效延迟为 20+node×10 us，回复为 100 us；读快照为 50 us，返回为 100 us。这些时延是共同先验，不能称为 N3 实测。实际动态写顺序逐键执行，阈值与许可分开，不是多节点原子更新。

新增 AccountedEngine 独立适配旧 EventEngine，消息费用为本地有效负载，API 费用按请求发行与回复返回分时归因。自有费用长度在调用完成后进入合法前缀，预测不得从执行器全量费用读取。失败费用通过正确性测试，主性能矩阵没有注入控制失败，不评价恢复性能。旧原型和数据保持原记录。

## 预算与指标

逐链路带内增量、导出尝试帧、API 序列化字节及本地消息有效负载独立核对，不将不同层的字节相加。禁用采样仍携带基础头。导出尝试计费包含未收到尝试，在线仅能估计其费用。

| 档位 | e1/e2 B/s | e3 B/s | 导出尝试 B/s | API B/s | 本地消息 B/s |
| --- | ---: | ---: | ---: | ---: | ---: |
| 低 | 180 | 300 | 540 | 1200 | 128 |
| 高 | 240 | 390 | 900 | 1200 | 128 |
| budget_floor 低 | 100 | 150 | 360 | 1200 | 128 |

本地消息 128 B/s 是预算，实际消息编码长度为 190 B，不是固定 128 B 消息。总窗口平均达标与单秒超限分开报告。本轮没有硬逐窗口监管。

连续损失为共享对象活跃区间的加权、截断归一化生成 AoI，截断值 4。有效接收新近度用于合法预测，不能称同步生成 AoI。覆盖表示各活跃任务区间内至少一个有效报告的加权比例，不是事件检测或路径重构准确率。

## 输入分组与原始记录

九场景为 stable、bursty、shared_priority、switch、sparse_stop、report_impairment、budget_floor、bursty_jitter 和 unannounced_switch。全部包含低、高预算和所有超限点。

| 分组 | 种子 | 实际执行 |
| --- | --- | ---: |
| 静态训练 | 11、29 | 118098 次完整回放，复用于两预算的 236196 条评分 |
| 动态校准 | 1301、1423 | 三方法各六参数，共 648 次 |
| 保留测试 | 1601、1723、1871、1999 | 八方法、九场景、两预算，共 576 次 |

保留业务在静态训练及动态参数冻结后生成。这六个校准/保留种子已经消费，后续重放不能重新称为未见输入。单组四个保留种子仅提供配对观察差值和范围，不提供统计显著性。

原始目录 [n3b-matched-01](../data/matched-reference/n3b-matched-01/manifest.json) 保存全部训练评分、校准、保留工作负载、逐包输出、十周期决策、费用前缀、候选、配置确认及执行源码。固定 12 个校准视图另有各 6561 配置的代理诊断，有限局部/联合分别与各自目标参照比较，不使用保留真值，也不是真实未来最优。

## 复现

~~~bash
python3 -m unittest discover -s tests -v
python3 experiments/check_matched_reference.py \
  --run data/matched-reference/n3b-matched-01 --workers 6
python3 experiments/analyze_matched_reference.py \
  --run data/matched-reference/n3b-matched-01 \
  --output data/matched-reference/新的分析目录 --workers 6
python3 experiments/plot_matched_reference.py \
  --run data/matched-reference/n3b-matched-01 \
  --analysis data/matched-reference/新的分析目录 \
  --output figures/matched-reference/新的绘图目录
~~~

独立检查不导入策略或账本，重建全部训练静态门控、费用、损失、冻结排序及校准/保留包事件、合法前缀和四层预算。训练快捷重建要求当前归档的无跨包出队交错、120 B 业务输入，该条件逐输入检查，不表示检查器适用于任意训练拓扑或负载。完整动态包核对仍使用全局出队事件顺序。

可以对已有保留案例重新执行模型，并核对除宿主求解耗时外的全部确定性内容：

~~~bash
python3 experiments/replay_matched_case.py \
  --run data/matched-reference/n3b-matched-01 \
  --scenario stable --budget high --seed 1601 --method phase_guard \
  --output data/matched-reference/新的单例重放目录
~~~

输出目录必须不存在。单例重放检查当前执行依赖与归档源码一致，不重新调参，不当成新测试样本。完整新分组入口 `run_matched_reference.py` 会核对历史实际消费，禁止以既有种子重复声明新保留矩阵；若启动新的研究矩阵，须先预声明新的分组。

图片由已核对汇总生成，PNG 为 300 dpi，PDF 嵌入真正的 Times New Roman。主图预选 static、joint、phase_guard、phase_local_guard，达标图展示全部八方法，完整费用 CSV 不筛选失败。

## 本次结果

全部 243 项单元检查通过。独立核对全部静态训练及校准/保留执行，保留包括 83840 个包执行实例、5760 次决策和 576 个覆盖值，固定代理诊断包括 78732 个候选。分析见 [n3b-analysis-01](../data/matched-reference/n3b-analysis-01/manifest.json)，完整费用见 [aggregate.csv](../data/matched-reference/n3b-analysis-01/aggregate.csv)。

| 方法 | 总窗口达标 / 72 |
| --- | ---: |
| static | 59 |
| feedback | 60 |
| local | 66 |
| joint | 66 |
| phase_joint | 68 |
| uniform_guard | 68 |
| phase_guard | 68 |
| phase_local_guard | 68 |

总计 523/576 达标，全部 53 个超限运行保留。低档 budget_floor 全部不达标，因为共享链路仅基础头已为 192 B/s，超过 150 B/s。总窗口达标仍可能单秒超限。

uniform_guard 在重点监测高档、稀疏停流两档、导出损伤高档和无通知切换高档五个单元中，四个配对种子均比静态损失低且双方达标。它在低档突发和有通知高档切换中也明显退化。phase_guard 没有四个配对种子均优于静态且双方达标的单元，但在规则突发中维持静态损失水平。全部差值见 [paired-comparisons.json](../data/matched-reference/n3b-analysis-01/paired-comparisons.json)。

收益包含实际成本。重点监测高档 uniform_guard 损失从 0.62803 降至 0.58921，但导出从 840 增至 898.125 B/s，另有 284.625 B/s API，平均四个单秒超限。稀疏停流 joint 损失从 2.47741 降至 2.35294，导出保持 165 B/s，API 增加 320.875 B/s。因此只能报告场景内的时效与费用权衡，不能宣称等实际消耗或普遍领先。

12 个时序修复校准视图中，联合有限搜索与同代理完整排序匹配 6 次，局部匹配 5 次。这是同一时序代理下两种搜索目标的诊断，不是八种部署方法各自的完整求解质量排名，不能沿用早期单视图一致结果声称一般最优。高档 budget_floor 与 stable 的输入/预算重复，稀疏停流两档结果相同，这些矩阵标签不是更多独立样本。

图见[损失 PNG](../figures/matched-reference/n3b-matched-01/matched-age-loss.png)、[PDF](../figures/matched-reference/n3b-matched-01/matched-age-loss.pdf)，以及[全部方法达标 PNG](../figures/matched-reference/n3b-matched-01/matched-budget-feasibility.png)、[PDF](../figures/matched-reference/n3b-matched-01/matched-budget-feasibility.pdf)。本轮没有新增网络运行，下一阶段接入真实主循环并实测求解、RPC 和轮询偏差。
