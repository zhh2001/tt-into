# 共同百毫秒观察的独立业务比较

本入口使用已冻结的八方法、共同初始化、六层预算及P00/P02/P05保护参数。报告读取100ms，编排与付费源计数一秒。四条新业务在各方法与两次独立网络部署中使用相同提供业务和任务，共256例。控制器实际可见的加权截断生成年龄损失为主指标，采集端另列。

准备和校验不生成业务或启动网络。准备时审计全部本地持久消费，从3001起选择最小四个未消费整数，固定两块顺序及全部执行源码。主矩阵开始后不再调参、提高预算或替换案例。每块128例，完整关闭第一块网络后才新建第二块。每例10秒，费用窗口[2,10)秒，年龄包含全部活跃任务区间及未知状态。

```bash
python3 experiments/prepare_observer_comparison_execution.py --output data/observer-comparison-plan/NEW-PREPARED-ID
python3 experiments/run_observer_comparison.py --plan data/observer-comparison-plan/NEW-PREPARED-ID/comparison-plan.json --output data/network-entry-runs/NEW-RUN-ID --validate-only
sudo python3 experiments/run_observer_comparison.py --plan data/observer-comparison-plan/NEW-PREPARED-ID/comparison-plan.json --output data/network-entry-runs/NEW-RUN-ID
```

主网络期间不并发同宿主回归、离线分析或第二网络测量。实际源计数、通知、配置确认及报告批次前缀计入合法视图。诊断读取和IPv4 ID仅用于离线核对。100ms观察不会额外触发计数RPC。工程失败停止并保留，费用超限或不可评价继续保留。

网络完全结束后运行完整回归并保存日志，然后统一分析。所有输出目录必须为新目录，禁止覆盖原始和失败运行。

```bash
python3 -m unittest discover -s tests -v > final-unit-tests.txt 2>&1
python3 experiments/analyze_observer_comparison.py --run data/network-entry-runs/NEW-RUN-ID --output data/observer-comparison-validation/NEW-ANALYSIS-ID
python3 experiments/check_observer_comparison_analysis.py --analysis data/observer-comparison-validation/NEW-ANALYSIS-ID --output independent-checks.json
python3 experiments/plot_observer_comparison.py --analysis data/observer-comparison-validation/NEW-ANALYSIS-ID --checks independent-checks.json --output figures/observer-comparison/NEW-FIGURE-ID
python3 experiments/validate_observer_comparison_delivery.py --analysis data/observer-comparison-validation/NEW-ANALYSIS-ID --figures figures/observer-comparison/NEW-FIGURE-ID --checks independent-checks.json --unit-log final-unit-tests.txt --output data/observer-comparison-audit/NEW-AUDIT-ID
```

公开分析保留256例、224动态/静态配对、112业务配对组、128部署重复组及28场景/方法汇总。四条业务是独立单位，两部署用于观察运行变异。全部双方达标状态、失败和未知费用保留。六层费用按各自字节定义分列，不跨层相加。条件年龄界用于表达时间测量范围，统计独立分母仍为四条业务。

图由脚本导出真正Times New Roman的300dpi PNG和嵌入字体PDF。实际收报到缓存进入等待和部署变异单独作诊断，避免将共同入口效果计为编排贡献。

## 本次记录

准备清单为`data/observer-comparison-plan/n4d-execution-01/`，完整分析为`data/observer-comparison-validation/n4d-comparison-01/`，独立交付为`data/observer-comparison-audit/n4d-comparison-01/`，正式图为`figures/observer-comparison/n4d-comparison-01/`。新业务3005–3008现均已消费，复跑准备入口会重新审计并选择下一组业务。

256例工程通过，40960包、27672报告、2560决策、1285成功字段更新，242例费用达标、12例超限、2例帧窗口费用界不可评价。4480付费计数读、70观察跳过和201条末段报告保留，决策没有跳过。启动458项、最终470项回归分列。独立1515669项网络、363911项分析、9868项配对及7453项交付核对通过。

重点监测phase_joint的四条业务两部署平均控制器结果均改善，整体控制器损失中点下降8.77%，采集端下降7.54%，8次双方达标。导出854.0625对838.125 B/s，增加1.90%，API354.375对0 B/s。稳定与抖动没有一致收益，切换体现预算与时效取舍。完整配对、费用状态、部署变化和不利结果均由脚本保存。
