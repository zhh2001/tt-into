# 有限静态重复确认

本入口执行 [冻结确认清单](../data/network-static-plan/n3f-confirmation-01/confirmation-plan.json)。四场景的模型参照与网络选择去重后共六组配置，两个已消费训练业务 11/29 各运行三个部署块，共 36 例。每块包含全部 12 单元，按原清单固定顺序执行，不扩大网格搜索。

每例 10 秒，2 秒预热，测量 [2,10) 秒。普通版本 6 P4、管理路径、字段、K=1、费用限额和 0/5 秒通知保持一致。复用 [有限静态训练的采集、时钟及费用仪表](README-network-static.md)，静态业务期间不做源计数读取或字段更新。安装、冷启动和末尾诊断保留分类。宿主观察包含控制器和四个 BMv2 进程的 CPU/RSS 端点，未包含发包和采集子进程 CPU。

`run_static_confirmation.py` 是独立入口，启动前核对确认清单摘要、实际配置与角色、三块顺序及原分析来源。旧 N3e 入口、清单与数据保持原样。`check_static_confirmation.py` 独立核对全部 36 例，缺少、重复或改变身份均不接受。所有输出写入新目录，工程失败保留并停止，预算失败继续保留。

`confirmation-scores.json` 保存六组完整评分，`frozen-static.json` 保存四场景的最终配置。每组必须六例费用上界全部达标且年龄有效。然后按最坏采集端年龄损失上界、六例平均上界、启用项数和配置编码排序。没有可行配置时报告有限集合不可行，不提高预算。后续方法共用这些配置作为初始化。

`seed-repeat-diagnostics.json` 将同一业务的三次部署分开，记录实际采样位置、年龄条件区间和中点观察范围。`candidate-repeat-pairs.json` 对两个配置发生变化的场景按业务和部署块配对，保留差值区间。包序号只用于离线验证。三块不是三个独立业务样本，年龄条件区间不是置信区间，微小排序差异不能直接归因于阈值改善。

```bash
sudo python3 experiments/run_static_confirmation.py --output data/network-entry-runs/<new-run-id>
python3 experiments/check_static_confirmation.py --run data/network-entry-runs/<new-run-id>
python3 experiments/analyze_static_confirmation.py --run data/network-entry-runs/<new-run-id> --output data/network-static-validation/<new-analysis-id>
python3 experiments/check_confirmation_analysis.py --analysis data/network-static-validation/<new-analysis-id>
python3 experiments/plot_static_confirmation.py --analysis data/network-static-validation/<new-analysis-id> --output figures/network-static/<new-figure-id>
```

分析、独立复核、最终回归及绘图在网络业务停止后执行。图片保留全部案例，以真正 Times New Roman 同时导出 300 dpi PNG 和嵌入字体 PDF。正式新测试业务在动态校准完成和比较合同冻结后另行声明。

## 保留结果和下一项准备

[n3f-confirmation-02](../data/network-static-validation/n3f-confirmation-02/manifest.json) 完成全部 36 例，交付 5760 包、4020 报告、21300 输出帧和 360 次决策，无在线源计数读、字段更新或周期跳过。36/36 条件窗口预算达标，主运行和最终回归均 299 项通过。[冻结配置](../data/network-static-validation/n3f-confirmation-02/frozen-static.json) 依次为稳定 C02、重点监测 C03、切换 C02、抖动 C05。

12 个配置/业务重复组的三块采样位置均相同，三块年龄区间有公共重叠。两个变更场景的 12 个候选配对也有相同采样位置和重叠区间，不将微小排序差异写成监测提升。抖动 C05 最坏上界略低，但平均上界高于 C01，仍采用预声明最坏优先规则。首次入口漏导入的启动失败仅本地保留，当时尚未产生网络业务。

重复图提供 [PNG](../figures/network-static/n3f-confirmation-02/static-confirmation-repeats.png) 和 [PDF](../figures/network-static/n3f-confirmation-02/static-confirmation-repeats.pdf)，配对条件差值图提供 [PNG](../figures/network-static/n3f-confirmation-02/static-confirmation-paired-differences.png) 和 [PDF](../figures/network-static/n3f-confirmation-02/static-confirmation-paired-differences.pdf)。图源、脚本及字体核查随图留档。

[动态网络校准清单](../data/network-dynamic-plan/n3g-calibration-01/calibration-plan.json) 保持首例前 prepared 快照。184 例已执行，结果和三种修复方法的全局参数见 [动态校准说明](README-dynamic-network-calibration.md)。1301/1423 已用于模型校准，与静态网络训练分开。全部方法使用最终共同初始化和相同字段、K、预算、合法信息接口。三种修复方法各六组预设，四种固定动态方法和静态参照完整保留。每修复方法选一个跨四场景的全局参数，有限排序和停止规则在清单内冻结，不扩大调参或提前生成正式测试。静态训练达标不保证迁移到校准业务仍达标，本轮切换的两个静态超限点完整保留。

```bash
python3 experiments/prepare_dynamic_network_calibration.py --analysis data/network-static-validation/n3f-confirmation-02 --output data/network-dynamic-plan/<new-plan-id>
```
