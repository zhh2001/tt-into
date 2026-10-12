# 冻结结果汇总图复现

五张图只调整呈现方式，不生成新业务、不改变策略或预算，也不修改已有分析和原始测量。全部使用真正的Times New Roman、同源300 dpi PNG及嵌入字体PDF。字体记录与输入、输出摘要见同目录文件。

| 图片 | 来源与口径 |
| --- | --- |
| main-controller-age | n4g-comparison-03全部320例，四场景十方法。均值条件界、八个部署点及预算超限红叉。 |
| main-priority-costs | 同源重点场景全部十方法的年龄与导出，以及静态、两联合方法、两文献适配的分层费用。各费用按自身预算归一化，不跨层相加。 |
| scale-controller-age | n5d-comparison-02全部432例，九单元六方法。未确认的n5f2稳定单元保留空缺。 |
| scale-software-cost | 同源实际软件诊断，按各单元案例数加权的平均决策耗时。 |
| sensitivity-response | n5e-comparison-01全部80例，五条件两方法，同时展示控制器与采集端年龄。 |

条件界反映测量时钟和可用性条件，不是统计置信区间。四独立业务及各两部署继续分别记录在源数据中。

从仓库根目录执行，使用不存在的新输出目录：

```bash
python3 experiments/plot_manuscript_results.py --output /tmp/tt-into-result-preview
```

代码拒绝覆盖现有输出目录。`manifest.json`记录所有输入文件摘要、源行数、字体和图片摘要。复现只需已公开的结果与软件诊断文件。
