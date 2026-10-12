# 静态规模候选三部署块确认

本入口继承data/network-scale-plan/n5b-confirmation-01冻结的20个单元候选、训练11/29及三个部署块，共120例。五拓扑、两场景，每拓扑每块八例，15次独立部署。候选、预算及顺序与冻结名单一致。

复用已验收的连续业务、100ms报告观察、1s静态决策、单节点共享任务及完整费用检查。每例冷复位，静态没有在线源计数读或字段更新。IPv4 ID在批内用于抓包验证，在线身份为FlowTag/Session。跨块同名案例以批次/案例关联，避免覆盖归档。

每候选六例全部工程与条件平均窗口within才具备共同初始化资格，按控制器上界最坏值、上界均值、实际导出均值和配置编码排序。全20评分及十个选择保留，无共同可行候选的单元保存未冻结状态。

## 复现

从当前源码生成新的短编号清单，冻结后保持执行源码不变。原名单为shortlist准备输入，真实执行快照独立保存。

```bash
python3 experiments/scale_confirmation_execution.py --mode confirmation --output data/network-scale-plan/n5c-repro-01
python3 experiments/run_scale_confirmation.py --plan data/network-scale-plan/n5c-repro-01/stage-plan.json --output data/network-entry-runs/n5c-repro-01 --validate-only
sudo python3 experiments/run_scale_confirmation.py --plan data/network-scale-plan/n5c-repro-01/stage-plan.json --output data/network-entry-runs/n5c-repro-01
python3 experiments/analyze_scale_confirmation.py --run data/network-entry-runs/n5c-repro-01 --output data/network-scale-validation/n5c-repro-01
python3 experiments/check_scale_confirmation_delivery.py --analysis data/network-scale-validation/n5c-repro-01
python3 experiments/plot_scale_confirmation.py --analysis data/network-scale-validation/n5c-repro-01 --output figures/network-scale/n5c-repro-01
```

主业务期间不并发同宿主回归、分析、绘图或第二网络。完整回归在正式建链前完成，逐例核对属于执行验收。观察跳过、预算超限、费用不可评价及部署变化全部保留。

烟测只重复三节点稳定C21/11在两个部署块中的同名案例，用mode smoke准备，不参与确认排名。frozen-static.json供后续六方法共同初始化，seed-repeat-diagnostics.json保留40个配置/业务的三块重复组。

## 实际结果

正式输入为data/network-scale-plan/n5c-execution-01，分析为data/network-scale-validation/n5c-confirm-01，独立交付为data/network-scale-audit/n5c-confirm-01。120/120工程通过，34560业务包、25856报告、202496输出帧。启动591项单元检查通过，分析949339项及独立交付938680项核对通过。

条件平均窗口117例within、2例exceeds、1例unavailable。两个超限均为第一部署块n5f2的C15/11，分别处于稳定和重点场景，导出907.5B/s对900B/s。第二块n5f2稳定C12/11的墙钟偏移条件交集不相容，帧费用界保留null，实际API仍为0。全部案例进入评分，没有替换运行。

| 拓扑 | 稳定初始化 | 重点初始化 |
| --- | --- | --- |
| 3节点/2流 | C21 | C22 |
| 5节点/2流 | 未取得共同确认资格 | C05 |
| 8节点/2流 | C41 | C41 |
| 5节点/4流 | C18 | C29 |
| 5节点/8流 | C38 | C38 |

20个候选评分中17个具备资格，冻结9个共同初始化。五节点两流稳定场景的两个候选分别受超限和费用不可评价影响，保留no_common_feasible_candidate，不据此推断整个配置空间不可行。八节点C41每例观察1/2/3/5/7/8，节点4/6没有报告，其共享年龄和全部任务分母仍保留。

1200次决策完整执行，11998/12000报告观察槽完成，两个跳过槽发生在第二块n5f8稳定C47/11的7.2s和7.3s。25595条新报告进入共享缓存，261条末段报告未进入视图。静态在线源计数和字段更新均为0，初始化及其他实际控制操作单独归档。

40个配置/业务的三块重复组中14组采样位置相同，采集端和控制器年龄各17组有共同条件交集，最大控制器损失中点范围0.189960。三个部署块重复已有11/29训练业务，独立业务分母为2。

figures/network-scale/n5c-confirm-01包含全部120例的年龄与导出图，真正Times New Roman、300dpi PNG及嵌入字体PDF同源导出。条件年龄条表示测量条件界。后续比较范围保存在data/network-scale-plan/n5d-scope-01，九单元、六方法、四新业务、两部署块规划为432例，当前没有生成新业务或冻结实际执行顺序。
