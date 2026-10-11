# 共同报告观察下的动态全局校准

报告入口100ms，决策及付费源计数一秒。static、feedback、local、joint、phase_joint、uniform_guard、phase_guard、phase_local_guard共同从控制器目标确认后的静态配置启动：stable/high C06、shared_priority/high C03、switch/low C04、bursty_jitter/low C11。具体阈值在准备目录的`frozen-static.json`。

只用已消费校准业务1301/1423。五固定方法各八例40例，三保护方法各六预设、每预设八例144例，共184例。预设门槛0/0.05/0.15和预留0/0.1按笛卡尔积编号P00–P05，四场景共用一个全局参数。执行顺序种子42426413。固定路径、字段和K=1，每例10秒，费用预热0–2秒，平均费用窗口[2,10)秒。

按非within案例数、六层归一化超限上界总和、平均控制器年龄损失上界、成功字段更新总数、参数索引排序。全部184例和18评分保留。费用分逐链路带内、导出尝试、API序列化及本地通知，不跨层相加。采集端年龄同时保存，用于解释生成、接收和控制器取得之间的差异。静态没有在线计数和更新，报告tick自身也不发起RPC。

## 执行和验证

准备和validate-only不生成业务或启动网络。重做使用新目录：

```bash
python3 experiments/prepare_observer_calibration.py \
  --output data/observer-calibration-plan/n4c-calibration-recheck-01
python3 experiments/run_observer_calibration.py \
  --plan data/observer-calibration-plan/n4c-calibration-recheck-01/calibration-plan.json \
  --output data/network-entry-runs/n4c-calibration-recheck-01 --validate-only
sudo python3 experiments/run_observer_calibration.py \
  --plan data/observer-calibration-plan/n4c-calibration-recheck-01/calibration-plan.json \
  --output data/network-entry-runs/n4c-calibration-recheck-01
python3 -m unittest discover -s tests -v
python3 experiments/analyze_observer_calibration.py \
  --run data/network-entry-runs/n4c-calibration-recheck-01 \
  --output data/observer-calibration-validation/n4c-calibration-recheck-01
python3 experiments/check_observer_calibration_analysis.py \
  --analysis data/observer-calibration-validation/n4c-calibration-recheck-01 \
  --output independent-checks.json
```

主网络期间不并发同宿主回归、离线分析或第二网络测量。工程失败停止并保留，预算失败继续并保留。首例前保存源码及prepared清单摘要，逐例归档真实观察批次、决策、付费读取、配置写、宿主端点及实际迟到。

公开产物含184例双位置年龄与六层费用、十八预设评分、三个冻结参数、64例选中设置、56个动态/静态业务配对、28个配对组、完整采样位置和观察等待。独立检查重新核对实际批次到决策映射、生成条件界、连续年龄积分、全局排序及配对。年龄条件界采用统一测量条件，业务分母为两条校准业务。

绘图脚本`plot_observer_calibration.py`以独立检查为入口，采用真正Times New Roman，导出同源300dpi PNG和嵌入字体PDF。`validate_observer_calibration_delivery.py`核对源码、数据、完整最终回归及字体。`prepare_observer_comparison.py`在校准通过后冻结共同八方法，保留四条新业务种子为待审计状态，不生成独立测试。

## 本次结果

最终公开分析为`data/observer-calibration-validation/n4c-calibration-02/`，交付为`data/observer-calibration-audit/n4c-calibration-02/`，方法冻结在`data/observer-comparison-plan/n4c-methods-01/`，两张正式图在`figures/observer-calibration/n4c-calibration-02/`。

184例工程通过，29440业务包、20011报告、1840决策、1049成功字段更新。181例平均费用达标、2例超限、1例帧窗口费用界不可用，全部保留。选中三个保护方法各8/8达标，全局参数为P00/P02/P05。重点监测phase_guard两业务均改善控制器新鲜度，损失中点均值下降9.47%，导出增加6.31%，API均值319.5 B/s，六层预算均达标。切换中动态方法达标但年龄较静态高，稳定与抖动的完整差别同样保留。

运行前439项、首次最终449项、最终450项回归日志分列。最终1133047项网络/控制/观察/缓存、148458项分析、201项全局排序、1366项配对及5438项交付核对通过。首次独立检查器漏处理空费用界，补齐后重建分析02，没有重跑网络。观察完成18384/18400槽，16跳过和201尾段报告保留。未来独立256例比较的方法已冻结，新测试业务尚未生成。
