# 文献机制的 TT-INT 适配

`intopp_due`采用INTOPP的完整期限依赖收益，`d_into_tt`采用D-INTO的信息收益、许可变化代价和虚拟队列。代码独立于已有local、feedback和phase_joint入口。原生二元容量子问题另外完整枚举，用于核对公式。

| 方法 | 依据 | 本原型保留与映射 |
| --- | --- | --- |
| intopp_due | [INTOPP](https://doi.org/10.1109/LCOMM.2019.2946562)，式1、3、5至7 | 单对象队列任务退化为单条目依赖，接收新近度超过期限时选择预计能完整采集的依赖。未知对象到期，重复及旧报告不推进缓存。可选预声明提前裕量和任务权重。 |
| d_into_tt | [D-INTO](https://doi.org/10.1109/TNSM.2025.3530432)，式3、4、6、10、25至26 | 管理权重乘预测频率达成率，按QΔ−VIn排序。Δ为12B乘许可位变化数，跨流迁移算两位。Q由相邻决策已确认许可结算。 |

共同100ms报告观察、1s决策及付费源计数，固定路径、字段和K=1，八阈值加禁用，48候选与6字段写。候选保留每对象最多一个搭载流。TT投影允许同路径的不同对象使用不同包机会，逐包限制记录数。该容量映射与原文单时隙条目字节求和有所不同，`native_rows`保留原生小实例容量核对。在线搜索是有限搜索，原生枚举的最优性不转移到TT搜索。

INTOPP的联合聚类与异常识别不在该适配中。D-INTO的收益是频率目标达成率，不是熵或真实信息量。两者使用付费计数EWMA的共同均匀机会预测，不接收真实发送轨迹、未来业务或生成时钟真值。接收新近度与真实生成AoI分列。虚拟队列是许可变化状态，阈值更新及实际API费用在独立账本保留。

运行正确性试点：

```bash
PYTHONPATH=src:experiments python3 experiments/prepare_literature_pilot.py \
  --output data/literature-adaptation-plan/<plan-id>
PYTHONPATH=src:experiments python3 experiments/run_literature_pilot.py \
  --plan data/literature-adaptation-plan/<plan-id>/observer-plan.json \
  --output data/network-entry-runs/<run-id> --validate-only
sudo env PYTHONPATH=src:experiments python3 experiments/run_literature_pilot.py \
  --plan data/literature-adaptation-plan/<plan-id>/observer-plan.json \
  --output data/network-entry-runs/<run-id>
PYTHONPATH=src:experiments python3 experiments/analyze_literature_pilot.py \
  --run data/network-entry-runs/<run-id> \
  --output data/literature-adaptation-validation/<run-id>
PYTHONPATH=src:experiments python3 experiments/check_literature_analysis.py \
  --analysis data/literature-adaptation-validation/<run-id> \
  --output <existing-audit-directory>/analysis-checks.json
```

目录必须不存在。计划冻结源码、共同初始化、参数和八案例顺序后才生成已消费训练11的业务。数据保存实收前缀、确认、付费调用、PCAP、宿主端点及执行快照。独立检查重建门控和容量预测、文献目标、确认队列、已评估候选排序和实际费用。正确性试点不选最终性能参数。

`prepare_literature_comparison.py`准备两个方法各六全局参数、96例训练、32例校准及后续十方法320例矩阵规格。训练11/29，校准1301/1423，旧八方法和共同初始化保持冻结。性能参数确定并完成入口后重新审计未消费业务，再冻结新测试；3001至3008已消费。准备规格没有生成新业务，也不代表已完成比较。

`n4e-pilot-01`的八例全部通过工程与条件平均窗口预算核对，1280业务包、670报告、44成功字段更新、160付费计数，800观察槽全部完成。启动与最终486项单元检查通过，网络/控制/观察134913项、独立分析5098项核对通过。两张图分别给出实际控制费用和D-INTO确认队列，保存同源TNR 300dpi PNG及嵌入字体PDF。试点参数用于正确性验收，后续按准备规格训练和校准。
