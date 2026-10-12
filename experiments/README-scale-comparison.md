# 六方法规模比较

固定九个已确认拓扑/场景单元、六方法、四独立业务和两个实际部署块，共432例。方法为static、feedback、phase_joint、phase_guard、INTOPP-TT和D-INTO-TT，初始化及全局参数继承data/network-scale-plan/n5d-scope-01。

节点轴为3/5/8节点两流，载体轴为五节点2/4/8流。五节点两流稳定场景没有取得共同初始化，其确认数据完整保留，正式规模矩阵在此处留空。重点场景包含全部五拓扑。

共同报告观察100ms、编排及付费源计数1s，固定字段与K=1。每节点一个共享任务，入口120F、公共195F、导出450F B/s，API1200和本地通知128B/s。12B基础头和未知年龄均保留，不按流复制收益。

## 执行和分析

从当前源码审计业务消费并准备新的短编号清单，保持冻结后源码不变。四新身份在真实业务生成前保存；部署重复不能替代独立业务分母。

```bash
python3 experiments/scale_comparison_execution.py --mode comparison --output data/network-scale-plan/n5d-repro-01
python3 experiments/run_scale_comparison.py --plan data/network-scale-plan/n5d-repro-01/stage-plan.json --output data/network-entry-runs/n5d-repro-01 --validate-only
sudo python3 experiments/run_scale_comparison.py --plan data/network-scale-plan/n5d-repro-01/stage-plan.json --output data/network-entry-runs/n5d-repro-01
python3 experiments/analyze_scale_comparison.py --run data/network-entry-runs/n5d-repro-01 --output data/network-scale-comparison-validation/n5d-repro-01
python3 experiments/check_scale_comparison_delivery.py --analysis data/network-scale-comparison-validation/n5d-repro-01
python3 experiments/plot_scale_comparison.py --analysis data/network-scale-comparison-validation/n5d-repro-01 --output figures/network-scale/n5d-repro-01
```

每块分别部署五拓扑，共十批。普通批48例、五节点两流批24例，每例冷复位。验证IPv4 ID按批内1000j分配，在线身份为FlowTag/Session。跨块同名案例按批次/案例二元身份保存。

正式网络创建前完成完整回归，主业务期间无同宿主回归、分析、绘图或第二网络。全部费用层、控制调用、报告观察批次、求解与宿主资源归档。预算超限、费用不可评价、观察跳过及退化结果进入完整分母。

分析独立重建360个动态/静态配对、180业务配对组、216部署重复和45动态方法单元。shared weighted age按全部任务计算，采集端与控制器端分列，条件年龄界与统计置信区间分开。四条业务各有两次部署，所有方法共用同一业务和预算。

有限连接烟测通过mode smoke准备，仅在五节点四流重点场景执行已消费训练11的static、phase_joint和D-INTO-TT三例，不参与正式性能排名。

## 已归档结果

正式prepared输入n5d-execution-02，独立业务3021–3024各两部署。公开数据network-scale-comparison-validation/n5d-comparison-02，独立交付及描述汇总network-scale-comparison-audit/n5d-comparison-02，图figures/network-scale/n5d-comparison-02。

432/432执行通过，432/432测量窗口预算within，130560业务包、96302报告、764462输出帧、4320决策、1726成功字段更新、13600付费源计数。启动604项回归、6333840项分析及6312600项独立重建通过。报告观察42189/43200完成，1011合法跳过及全部944条未进入视图尾段保留。

重点双流五/八节点phase_guard相对冻结静态参照的控制器年龄损失分别下降29.81%/25.00%，导出765→876.563及682.5→795 B/s，新增API349.406/349.016 B/s。八节点两场景两联合覆盖全部共享节点。五节点四流重点D-INTO-TT均值低于两联合方法，完整方法配对保留。

五节点八流两联合的控制器年龄高于静态，尽管采集端年龄更低。八流占1011观察跳过中的962，decide均值约207ms，体现当前同步软件控制循环的规模成本。年龄、覆盖和各层费用分别解释，测量条件界不作统计置信区间。

首次comparison-01因独立文献核对器变量覆盖完整节点集合而中断，原状态和实际输入保留于network-scale-comparison-failures/n5d-interrupted-01。新的check_scale_comparison_literature保留完整基础头链路集合，五失败案例67752项定向核对及历史稀疏配置回归通过；原方法、参数、初始化和预算保持不变。3017–3020已消费，正式02重新审计身份，不混入完整排名。

有限敏感性范围已冻结于data/network-scale-sensitivity-plan/n5e-scope-01，80例、五节点四流重点、static与phase_guard，现已完成全部80例，复现及结果见README-sensitivity.md。
