# 有限预算与共同观察周期敏感性

采用五节点四流重点共享任务、static及phase_guard，继承规模比较的共同C29和全局P02。所有条件保持相同初始许可/阈值，不重新训练。四独立业务各两部署、五条件共80例。预声明范围为data/network-scale-sensitivity-plan/n5e-scope-01。

| 条件 | 带内及导出预算乘数 | 报告周期 | 每例报告槽 |
| --- | --- | --- | --- |
| B085 | 0.85 | 100ms | 100 |
| B100 | 1.00 | 100ms | 100 |
| B115 | 1.15 | 100ms | 100 |
| O250 | 1.00 | 250ms | 40 |
| O1000 | 1.00 | 1000ms | 10 |

名义入口480、公共780、导出1800 B/s。乘数仅作用于带内和导出，API1200 B/s及本地通知有效负载128 B/s保持独立。固定K=1、48候选、6字段写，一秒编排与付费源计数不变。报告tick不增加源计数RPC。各条件和方法对同业务使用相同发包、任务及通知，未知任务和基础头费用保留。

## 复现

准备时扫描全部data持久消费证据，选择四条新业务，保存prepared顺序和完整源码摘要。两块分别建立相同网络，每块40例。案例IPv4 ID仅供逐帧验证，在线身份为FlowTag/Session。冻结后保持源码不变。

```bash
python3 experiments/sensitivity_execution.py --mode comparison --output data/network-scale-sensitivity-plan/n5e-repro-01
python3 experiments/run_sensitivity.py --plan data/network-scale-sensitivity-plan/n5e-repro-01/stage-plan.json --output data/network-entry-runs/n5e-repro-01 --validate-only
sudo python3 experiments/run_sensitivity.py --plan data/network-scale-sensitivity-plan/n5e-repro-01/stage-plan.json --output data/network-entry-runs/n5e-repro-01
python3 experiments/analyze_sensitivity.py --run data/network-entry-runs/n5e-repro-01 --output data/network-scale-sensitivity-validation/n5e-repro-01
python3 experiments/check_sensitivity_delivery.py --analysis data/network-scale-sensitivity-validation/n5e-repro-01
python3 experiments/plot_sensitivity.py --analysis data/network-scale-sensitivity-validation/n5e-repro-01 --output figures/network-scale/n5e-repro-01
```

网络创建前完成完整单元回归，主业务期间无同宿主回归、分析、绘图或第二网络。独立核对实际观察槽、读取完成、跳过、缓存推进、十次决策前缀和真实付费调用。主指标为全部共享任务的控制器加权截断生成年龄，同时保存采集端年龄、覆盖、逐层费用和宿主资源。

结果包含40个同条件方法配对、64个相对B100的条件配对、52个业务组、13个聚合组及40个部署重复。预算超限、费用不可评价、退化及合法跳过保持完整分母。方法效应和共同观察入口效应分别解释，测量条件界不作为统计置信区间。

mode smoke仅使用已消费训练11，对B085和O1000各执行两方法，共四例。烟测验收真实入口及费用，不参与正式排名。正式数据及图由完整矩阵脚本生成，TNR PNG300dpi与嵌入字体PDF同源。

## 已归档结果

正式输入n5e-execution-01在业务生成前审计432份清单和2894份持久业务证据，冻结四新业务3025–3028、384项源码和两块顺序。正式公开数据network-scale-sensitivity-validation/n5e-comparison-01，独立交付network-scale-sensitivity-audit/n5e-comparison-01，三张正式图figures/network-scale/n5e-comparison-02。

80/80工程及平均窗口预算通过，25600包、12791报告、140791输出帧、800决策、168成功字段更新、1600付费源计数。620项启动单元回归、629295项分析和626155项独立交付通过。5600计划观察槽完成5599，唯一合法跳过和全部400任务区间保留，无决策跳过。12410条新报告进入共享缓存，381条尚未进入计划视图的尾段完整保存。

三档预算下phase_guard控制器平均年龄损失相对静态下降6.30%、6.29%、6.83%，各档四独立业务平均效应均为正。最低预算仍有余量，评价为冻结运行点的有限预算响应。名义预算100ms下导出static/guard872.813/1044.375 B/s，guard API544.063 B/s，改善同时增加实际费用。

250和1000ms条件下，同条件方法改善4.69%和2.72%，但guard控制器损失相对100ms分别增加36.26%和148.66%，静态同样退化，采集端变化较小。公共观察入口与编排方法效果按独立配对解释。40方法配对均双方达标、39条件差值更低、一配对界重叠，全部业务和部署分母保留。

每例实际初始通知有效负载310B，在预热期计费，测量窗口通知费用为0不表示无通信。最大读取4.423ms、decide177.639ms、在线API1.920ms、源迟到4.252ms、报告至状态更新上界986.575ms。40重复组采样/决策一致性分别1/20组，完整记录部署变化。

运行结束后仅调整绘图纵轴留白，正式图02由完整相同数据生成，实际执行和分析源码快照保留原绘图版本。核对数据使用归档源码与产物绑定，复现新的运行先重新prepared。
