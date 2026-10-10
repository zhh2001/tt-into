# 有限候选的真实网络静态训练

本入口使用已经冻结的 [24 候选清单](../data/network-static-plan/n3e-candidates-01/candidate-plan.json)，在 stable/high、shared_priority/high、switch/low、bursty_jitter/low 的两个已消费训练业务 11/29 上各执行一次，共 192 案例。它用于检查模型配置迁移和加强静态参照，不是未消费业务测试，也不能据 24/6561 的子集宣称全网格最优或网络充分调参。

## 执行合同

每例 10 秒，2 秒预热，测量 [2,10) 秒，固定管理路径、元数据字段、K=1 和原费用限额。八块分别对应场景和业务种子，每块含全部 24 候选。块顺序种子为 22360679，块内种子为 24494897 至 24494904。执行前完整顺序、业务、任务、候选摘要和源码快照一起留档。烟测只含稳定高档种子 11 的全禁用与全 50 ms 配置，不参与主选择。

普通版本 6 P4 沿用 [网络校准的文件前缀和时间合同](README-network-calibration.md)。许可关闭仍保留基础头，不自动释放寄存器。安装和冷启动费用入账，静态业务期间不做无用途源计数读取或配置更新。末尾读取只用于诊断。初始及切换通知仍在 0/5 秒实际发送，可用时刻以真实接收和状态取得为准。

逐案例记录控制器、四个 BMv2 进程 CPU tick 和 RSS 端点，以及宿主负载和内存。范围包含安装与核对，不包括采集/发包子进程 CPU，不把 RSS 端点当峰值或 BMv2 开销当硬件性能。主矩阵期间避免同宿主单元回归、离线分析或另一网络测量，观察记录不能完全排除外部调度干扰。

## 费用、年龄和选择

全部候选保留，包括全禁用、超限、不确定和不可评价结果。全禁用允许没有报告或缓存，全部任务仍在分母，未知截断归一年龄损失为 4。带内逐链路、导出尝试、API 字节和本地 UNIX 通知有效负载分别计量。抓包时间转换采用恒定墙钟偏移条件交集和原始精度，完成窗口达标不构成在线硬预算。

`summary.csv` 和 `generation-bounds.json` 给出采集端实收的生成 AoI 条件界。`controller-visible-bounds.json` 和 `case-availability.csv` 另给出实际信息进入状态后的年龄。共享对象不按流重复计收益，重复及旧报告不刷新缓存，全部活跃任务保留。不存在样本的节点无需推断时钟偏移，不据此删除全禁用配置。

主选择使用采集端年龄，先要求两种子全部费用上界达标且年龄界有效。依次最小化两业务最坏损失上界、平均上界、启用项数和配置编码。`candidate-scores.json` 保存 96 个配对评分，`network-selections.json` 保存四单元选择及冻结模型参照。没有共同可行候选时报告有限集合不可行，不能提高预算或选择失败配置。

## 运行与复核

环境和普通 P4 见 [版本 6 说明](README-into-validation.md)。候选来源和原训练输入摘要在启动前核对，当前入口还固定原候选文件摘要，不重选候选。所有输出均使用新目录，原始编译 JSON 含本机源路径，仅留原始目录，不复制到公开分析。

```bash
sudo python3 experiments/run_network_static.py --output data/network-entry-runs/<new-run-id>
python3 experiments/check_network_static.py --run data/network-entry-runs/<new-run-id>
python3 experiments/analyze_network_static.py --run data/network-entry-runs/<new-run-id> --output data/network-static-validation/<new-analysis-id>
python3 experiments/check_static_analysis.py --analysis data/network-static-validation/<new-analysis-id>
python3 experiments/plot_network_static.py --analysis data/network-static-validation/<new-analysis-id> --output figures/network-static/<new-figure-id>
```

业务或来源核对破坏属于工程失败，停止并保留原始目录、失败及未完成的声明分母。预算失败继续保留，不缩减为只完成优秀候选。分析与最终回归在网络测量停止后进行。图片同时导出真正 Times New Roman 的 300 dpi PNG 和嵌入字体 PDF。

## 保留结果与迁移复核

[n3e-static-01](../data/network-static-validation/n3e-static-01/manifest.json) 的 192 案例全部工程通过，交付 30720 包、收报 18734、恢复 110894 输出帧，1920 次决策，无在线更新或周期跳过。168/192 条件窗口达标，24 超限案例完整保留。四单元的共同可行候选分别为 21/24、21/24、18/24、24/24。主运行及分析回归均为 280 项。

稳定和切换选中原模型配置。重点监测与抖动突发的网络选择发生变化，但最坏年龄上界仅相差约 0.000228 和 0.001102，条件区间重叠。[采样位置诊断](../data/network-static-diagnostics/n3e-transfer-01/findings.json) 的八个配对使用相同业务中的包序号归一，采样节点集合全部相同，不把微小差距归为新增采样机会改善。包序号仅离线验证，策略不读取。迁移诊断另有四项专项检查通过。

```bash
python3 experiments/diagnose_static_transfer.py --analysis data/network-static-validation/n3e-static-01 --output data/network-static-diagnostics/<new-diagnostic-id>
python3 -m unittest discover -s tests -p test_static_transfer.py -v
```

全部候选及排除点图为 [PNG](../figures/network-static/n3e-static-01/static-candidates-age-budget.png) 和 [PDF](../figures/network-static/n3e-static-01/static-candidates-age-budget.pdf)，两处年龄图为 [PNG](../figures/network-static/n3e-static-01/static-model-network-observers.png) 和 [PDF](../figures/network-static/n3e-static-01/static-model-network-observers.pdf)。两种子均值条件区间不是置信区间，不能从报告数推导统计独立性。

[36 例确认清单](../data/network-static-plan/n3f-confirmation-01/confirmation-plan.json) 已 prepared，尚未执行。它只包含四单元的模型参照与网络选择去重后的六个配置，两个训练业务各三个部署重复，不追加网格搜索或消费新测试业务。

```bash
python3 experiments/prepare_static_confirmation.py --analysis data/network-static-validation/n3e-static-01 --diagnostic data/network-static-diagnostics/n3e-transfer-01 --output data/network-static-plan/<new-confirmation-plan-id>
```
