# 十方法独立业务比较复现

十方法比较沿用共同100ms报告观察、1s决策及付费计数、固定路径、字段、K=1和实际分层预算。方法包括静态TT-INT、七种动态参照和INTOPP-TT、D-INTO-TT两个文献适配。共同初始化及旧方法参数沿用已冻结合同，文献适配固定全局P4/P5，参数来源见[训练与校准说明](README-literature-training.md)。

四场景、四独立业务、两个独立网络部署，共320例。每块160例，先关闭第一块再建立第二块。独立业务分母是四，两个部署用于考察运行变化。两位置年龄分别计算，实际链路、导出、API和本地通知费用分列。所有预算失败、费用不可评价、任务未知区间及观察跳过均保留。

本轮分析合同为`data/literature-comparison-plan/n4g-analysis-04/analysis-contract.json`。执行清单为`data/literature-adaptation-plan/n4g-execution-01/stage-plan.json`，四新业务3013至3016在全局参数冻结后经消费审计选出，现已消费。新入口在业务生成前再次检查消费，已执行的业务不能重复称为未见测试。重新独立比较时使用`prepare_literature_comparison_execution.py`重新审计并冻结包含新验收源码的计划，其`--failed`参数指向完整保留的原中断目录。旧`prepare_literature_stage.py`冻结的是修复前入口，不能直接用于新运行程序。

重新测量时先建立新的未消费业务清单，再冻结分析合同。下列`<fresh-plan>`指向新清单，输出目录必须不存在。真实业务运行期间保持同宿主没有回归、离线分析、绘图或第二网络测量。已有3013至3016清单用于核对既有结果，不能再次作为新独立测试启动。

```bash
PYTHONPATH=src:experiments python3 experiments/literature_comparison_analysis_contract.py \
  --plan data/literature-adaptation-plan/<fresh-plan>/stage-plan.json \
  --output data/literature-comparison-plan/<analysis-plan-id>
sudo env PYTHONPATH=src:experiments python3 experiments/run_literature_comparison.py \
  --plan data/literature-adaptation-plan/<fresh-plan>/stage-plan.json \
  --output data/network-entry-runs/<run-id>
```

网络关闭后统一分析。独立检查器的`--output`是文件路径，需要先建立其父目录。

```bash
PYTHONPATH=src:experiments python3 experiments/analyze_literature_comparison.py \
  --run data/network-entry-runs/<run-id> \
  --contract data/literature-comparison-plan/<analysis-plan-id>/analysis-contract.json \
  --output data/literature-comparison-validation/<run-id>
PYTHONPATH=src:experiments python3 experiments/check_literature_comparison_analysis.py \
  --analysis data/literature-comparison-validation/<run-id> \
  --output <existing-check-directory>/independent-checks.json
PYTHONPATH=src:experiments python3 experiments/plot_literature_comparison.py \
  --analysis data/literature-comparison-validation/<run-id> \
  --checks <existing-check-directory>/independent-checks.json \
  --output figures/literature-comparison/<run-id>
```

分析完整保存288动态/静态配对、144业务配对组、160部署重复组、36场景/动态方法组及40方法单元。静态空参数、旧方法空文献预设和两个固定文献预设均明确核对，完整同业务输入与任务单独检查。预算达标资格与条件差值分开，条件界按实际生成及观察时间计算。

`check_literature_comparison_delivery.py`绑定全部测量和源码、启动/最终回归、独立检查和五张正式数据图。图保存同源Times New Roman 300dpi PNG与嵌入字体PDF，分别展示两位置年龄、重点监测配对取舍、观察/部署诊断及六层费用。

原n4g-comparison-01在第264例因旧校验器错误要求跳过周期含view而中断，263例完成记录及失败原始记录完整保留。修复使用独立新验收模块，保持原策略、任务、预算和预设。重新审计后用3013至3016完整执行新320例矩阵。公开失败附件data/literature-comparison-failures/n4g-interrupted-01包含真实跳过周期、完整线格式与调用，可复现新校验器的时序及无动作检查。

最终交付data/literature-comparison-validation/n4g-comparison-03，320例全部工程通过，311例条件平均窗口达标，9例超限保留。共51200包、33012报告、3200决策和1739成功字段更新。独立核对2604258项网络/来源、447305项分析及12892项配对检查。主运行启动517项回归，诊断入口修复后最终518项回归通过。原分析合同03和修复后04均保留，九份主要统计产物完全一致。五张正式图位于figures/literature-comparison/n4g-comparison-03，完整交付核对位于data/literature-comparison-audit/n4g-comparison-03。

直接核对随仓库交付的完整结果时，使用`check_literature_comparison_analysis.py --analysis data/literature-comparison-validation/n4g-comparison-03`。它从随产物保存的抓包、调用、输入和任务重建结果，无需再次启动网络。原始及修复后分析合同明确记录生成前冻结与测量后诊断修复的时间顺序。
