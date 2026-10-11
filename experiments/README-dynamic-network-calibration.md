# 有限动态网络参数校准

本入口执行 [冻结清单](../data/network-dynamic-plan/n3g-calibration-01/calibration-plan.json)，保持 [最终静态参照](../data/network-static-validation/n3f-confirmation-02/frozen-static.json) 作为全部八方法的共同初始化。配置集合、字段、K=1、48 候选、6 字段写限额、预算和合法观察接口相同。

校准业务为 1301/1423，已用于模型校准，与静态网络训练 11/29 分开。每例 10 秒、2 秒预热、测量 [2,10) 秒，需求通知在 0/5 秒实际发送。在线只取得完整实收文件前缀、付费返回计数、确认历史、实收通知和自有完成费用。通知实际可用、逐字段顺序生效、成功门控保留及部分失败沿用 [既有网络合同](README-network-calibration.md)。发送仪表和未来业务仅用于离线核对。

三种修复方法 uniform_guard、phase_guard、phase_local_guard 各有相同六预设，为动作门槛 0/0.05/0.15 与预算预留 0/0.1 的组合。每预设在四场景、两业务各执行一次，共 144 例。固定反馈、局部、联合和相位联合方法共 32 例，静态参照 8 例，共 184 例。全部顺序在清单中冻结，不按已有成绩调整。排序种子不增加业务样本数。

每修复方法只选择一个跨四场景的全局参数。先要求八例完整且费用和年龄可评价，再依次最小化非 within 案例数、六层归一超限总和、年龄损失上界均值、成功字段更新数和参数索引。归一超限逐例逐层计算 max(费用上界率/限额−1,0)，带内三链路、导出尝试、API 字节和 UNIX 消息有效负载分别计算。未知费用不算零。没有全部达标的预设时仍报告有限最佳及 frozen_with_budget_failures，不提高预算或隐藏失败。

`parameter-scores.json` 保存全部 18 全局评分，`frozen-parameters.json` 保存三种修复方法的参数。`selected-calibration-cases.json` 保留固定方法、静态参照和各方法全局参数的完整展示集合，正常可评价时为 64 例。该集合参与过校准选择，不能称独立测试。

```bash
python3 experiments/run_dynamic_network_calibration.py --validate-only --output /tmp/unused-network-calibration-validation
sudo python3 experiments/run_dynamic_network_calibration.py --output data/network-entry-runs/<new-run-id>
python3 experiments/check_dynamic_network_calibration.py --run data/network-entry-runs/<new-run-id>
python3 experiments/analyze_dynamic_network_calibration.py --run data/network-entry-runs/<new-run-id> --output data/network-dynamic-validation/<new-analysis-id>
python3 experiments/check_dynamic_calibration_analysis.py --analysis data/network-dynamic-validation/<new-analysis-id>
python3 experiments/diagnose_dynamic_calibration.py --analysis data/network-dynamic-validation/<new-analysis-id> --output data/network-dynamic-diagnostics/<new-diagnostic-id>
python3 experiments/plot_dynamic_network_calibration.py --analysis data/network-dynamic-validation/<new-analysis-id> --output figures/network-dynamic/<new-figure-id>
```

独立核对包含全部方法/参数/业务身份、完整顺序、实际参数和共同初始化，不只核对总数。所有输出使用新目录，工程失败停止并保留，预算失败继续保留。旧入口、清单、源码快照和数据保持原样。普通编译 JSON 含本机源路径，只留原始目录，公开分析保留其摘要。

主网络业务期间避免同宿主回归、离线分析或另一组网络测量，逐例保留控制器和四个 BMv2 的 CPU/RSS 端点与宿主观察。分析和最终回归在网络停止后执行。真实墙钟/单调条件区间和报告进入视图的时间仍分列，不宣称硬同步、在线硬预算或硬件性能。

数据图保留全部参数、预算失败及选中配置，两处年龄分列，导出真正 Times New Roman 的 300 dpi PNG 与嵌入字体 PDF。条件区间不是置信区间，两个校准业务也不足以作一般性能排名。正式比较的未消费测试清单在参数和评价合同固定后另行生成。

## 保留结果

[n3g-calibration-01](../data/network-dynamic-validation/n3g-calibration-01/manifest.json) 完成全部 184 例工程核对，保存 29440 包、20210 报告、108530 输出帧、1840 次决策、979 次成功字段更新，未跳过周期。启动和最终回归均为 315 项通过，新增 16 项。独立线格式/配置/控制核对 727907 项，可用性核对 35834 项，缓存/预算来源 9200 项，汇总与全局选择核对 100415 项。

182/184 条件窗口达标。两个超限案例均为 switch/low/static，e3 为 301.5 B/s，超过 300 B/s，导出为 547.5 B/s，超过 540 B/s。它们使用冻结训练配置，不据校准结果重新调参或提高预算。动态方法在这两项业务达标，但年龄损失更高，保留预算与时效的取舍。

[冻结全局参数](../data/network-dynamic-validation/n3g-calibration-01/frozen-parameters.json) 为 uniform_guard P03（门槛 0.05、预留 0.1）、phase_guard P02（0.05、0）和 phase_local_guard P04（0.15、0）。每方法六组预设均八例达标，选择仍严格采用原排序规则，不逐场景选择。完整 18 组评分和全部 184 例仍是分析分母。

[配对诊断](../data/network-dynamic-diagnostics/n3g-pairs-01/paired-cases.json) 从选中展示集合核对 56 个同业务动态/静态配对，42 个双方达标，16 个采样位置相同。重点监测高档中 phase_joint、uniform_guard、phase_guard 的两个业务均有较低的采集端年龄条件差值，同时增加导出或 API 费用。稳定及抖动场景没有共同的改善证据，局部方法和部分联合方法存在退化。相位局部保护虽全局达标，重点监测年龄仍高于静态。上述配对参与过校准，不能替代独立业务检验，也不能将采集端收益等同于控制器视图收益。

全局参数图提供 [PNG](../figures/network-dynamic/n3g-calibration-01/dynamic-global-parameter-calibration.png) 和 [PDF](../figures/network-dynamic/n3g-calibration-01/dynamic-global-parameter-calibration.pdf)，两观察位置图提供 [PNG](../figures/network-dynamic/n3g-calibration-01/dynamic-selected-calibration-observers.png) 和 [PDF](../figures/network-dynamic/n3g-calibration-01/dynamic-selected-calibration-observers.pdf)。图源、脚本、摘要和字体核对随图保留。

主矩阵期间没有同宿主回归、离线分析或第二组网络测量。184 例条件时钟交集兼容仍不是硬同步。报告接收至实际状态更新观察上界最大约 988.432 ms，单次求解最大约 89.330 ms，API 调用最大约 4.057 ms，源发送迟到最大约 4.676 ms。这些是本机软件环境诊断。通知长度取实际字节，本轮切换通知为 190 B 有效负载，不沿用训练业务的 192 B，也不称网络帧。

下一阶段固定以上参数、静态参照、八方法与完整评价口径后，建立新的独立业务清单。当前尚未生成或消费正式新测试业务。

[比较方法合同](../data/network-comparison-plan/n3h-methods-01/method-contract.json) 已为 `methods_frozen`，保留八方法四场景，首轮四个新业务各两个部署块，共 256 例。它还没有业务种子、执行源码和完整顺序，`execution_ready=false`，不能直接当网络运行清单。独立入口与历史业务消费审计完成后，按固定规则选择新业务并冻结完整清单。

```bash
python3 experiments/prepare_network_comparison.py --analysis data/network-dynamic-validation/n3g-calibration-01 --output data/network-comparison-plan/<new-method-contract-id>
```
