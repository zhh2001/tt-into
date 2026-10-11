# 文献适配参数训练与校准复现

本入口把两种独立文献适配方法接到共同的报告观察、控制账本和真实网络执行器。`intopp_due` 对应 INTOPP-TT，`d_into_tt` 对应 D-INTO-TT。机制映射见 [README-literature-adaptation.md](README-literature-adaptation.md)。参数按全部场景统一选择，各场景共用同一预设。

已交付的 `n4f-training-01` 和 `n4f-calibration-01` 共128例工程及条件平均窗口预算全部通过，20480业务包、10622报告、689次成功字段更新。最终冻结INTOPP-TT P4（提前裕量比例1、依赖不加权）和D-INTO-TT P5（V=432、平均许可变化12B）。训练/校准分别保留11/1个观察槽跳过，全部任务和尾段报告留档。启动与最终498项单元检查通过，独立分析及全局排序通过。四张正式图保存同源TNR PNG/PDF。

`n4f-comparison-01` 已准备320例十方法矩阵，3009至3012为经消费审计冻结的新业务，尚未生成或执行。训练与校准用于选参，独立比较将另行报告性能。

报告观察间隔为 100 ms，编排及付费源计数间隔为 1 s。固定路径、字段、K=1、阈值网格、预算和共同初始化沿用共同观察比较合同。训练覆盖四场景、业务种子 11/29、两方法各六预设，共 96 例。每方法选出两个全局候选，再用 1301/1423 校准，共 32 例，各冻结一个全局预设。

每个预设均含四场景和两业务的八次运行。排序依次使用预算未达标例数、最坏场景的控制器年龄损失上界均值、全部运行上界均值、平均 API 序列化字节数和预设编号。费用不可评价计入未达标，全部任务、跳过观察和失败运行保留。两个位置的年龄分别汇总，API、三条链路、导出尝试与本地通知费用分别计量。

## 执行

在仓库根目录执行以下命令。`--output` 必须指向新目录，已有产物不会覆盖。网络入口需要管理员权限。运行真实业务期间，保持同宿主没有第二网络、回归测试或离线分析。

```bash
PYTHONPATH=src:experiments python3 experiments/prepare_literature_stage.py \
  --stage training --output data/literature-adaptation-plan/reproduce-training

sudo env PYTHONPATH=src:experiments python3 experiments/run_literature_matrix.py \
  --plan data/literature-adaptation-plan/reproduce-training/stage-plan.json \
  --output data/network-entry-runs/reproduce-training
```

网络停止后汇总，并独立重建线格式、合法来源、实际费用、观察映射和完整任务年龄。检查器的 `--output` 是 JSON 文件路径，其父目录需要先建立。

```bash
mkdir -p data/literature-training-audit/reproduce
PYTHONPATH=src:experiments python3 experiments/analyze_literature_matrix.py \
  --run data/network-entry-runs/reproduce-training \
  --output data/literature-training-validation/reproduce-training
PYTHONPATH=src:experiments python3 experiments/check_literature_matrix_analysis.py \
  --analysis data/literature-training-validation/reproduce-training \
  --output data/literature-training-audit/reproduce/training-analysis-checks.json
PYTHONPATH=src:experiments python3 experiments/freeze_literature_parameters.py \
  --analysis data/literature-training-validation/reproduce-training \
  --checks data/literature-training-audit/reproduce/training-analysis-checks.json \
  --output data/literature-training-selection/reproduce-training
PYTHONPATH=src:experiments python3 experiments/check_literature_selection.py \
  --selection data/literature-training-selection/reproduce-training \
  --output data/literature-training-audit/reproduce/training-selection-checks.json
```

通过训练验收后，准备校准计划。使用同一网络执行、分析与独立检查入口，另建校准目录。

```bash
PYTHONPATH=src:experiments python3 experiments/prepare_literature_stage.py \
  --stage calibration \
  --selection data/literature-training-selection/reproduce-training/selection.json \
  --output data/literature-adaptation-plan/reproduce-calibration
```

校准冻结后才能准备十方法比较。准备入口重新检查本地持久化业务消费记录，选择四个未消费业务种子。两个独立部署块共 320 例，业务分母为四，旧八方法及共同初始化保持原合同。准备和 `--validate-only` 不生成测试业务。

```bash
PYTHONPATH=src:experiments python3 experiments/prepare_literature_stage.py \
  --stage comparison \
  --selection data/literature-training-selection/reproduce-calibration/selection.json \
  --output data/literature-adaptation-plan/reproduce-comparison
PYTHONPATH=src:experiments python3 experiments/run_literature_matrix.py \
  --plan data/literature-adaptation-plan/reproduce-comparison/stage-plan.json \
  --output data/network-entry-runs/reproduce-comparison --validate-only
```

## 图片

`plot_literature_training.py` 根据完整分析和冻结选择绘制全局评分及六层费用。每张图输出同源 300 dpi PNG 和嵌入 Times New Roman 的 PDF。星号表示全局排序选中预设，测量条件界按共同时间口径计算。

```bash
PYTHONPATH=src:experiments python3 experiments/plot_literature_training.py \
  --data data/literature-training-validation/reproduce-training \
  --selection data/literature-training-selection/reproduce-training \
  --output figures/literature-training/reproduce-training
```
