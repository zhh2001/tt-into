# 共同观察入口的控制器目标静态训练

本入口承接已验收的100ms报告观察，编排和付费计数仍为一秒。训练静态配置时主指标为控制器实际可用的共享任务生成AoI归一化截断损失，clip=4。采集端年龄另列，全部真实任务、活跃区间和未知时段保留。

## 固定范围与选择规则

沿用原prepared有限静态候选，四场景各24个，不从本轮结果扩充候选。每个候选使用已消费训练11/29各一次，共192例。每例10秒，前2秒预热，费用窗口[2,10)秒。训练与校准/新测试分开，当前入口不会生成未见测试业务。

只有两个业务均有有效年龄且六层费用的条件平均窗口均within，才进入选择。排序依次为最坏控制器年龄损失上界、平均上界、启用项数及配置编码。超限、不确定、不可评价和全禁用结果全部保留。选中状态为`training_selected_pending_confirmation`，重复确认完成前不能称共同初始化已经冻结。

费用前沿采用六层最坏业务费率和最坏控制器年龄上界，共七维。逐维不高且至少一维严格较低才构成支配。预算失败点仍保留。导出单轴图只是该向量的投影。24/6561有限子集不能称全网格最优，更多导出也不能称等费用提升。

共同开发协议保留static、feedback、local、joint、phase_joint、uniform_guard、phase_guard和phase_local_guard八法。三个保护方法保留六个全局预设，后续完整184例校准尚待执行。正式256例新业务比较须在初始化和参数冻结后重新审计并冻结业务身份。本阶段消费审计中的未用候选不是正式测试清单。

## 调度与费用

每例有100个报告槽和10个决策槽。完整迟到一个报告槽时记录跳过，不补更新。决策引用截止前最新完成的批次。重复及旧报告不重置缓存，部分行不提前使用，末段归档也不补在线年龄。

报告tick不发起RPC。静态没有在线源计数读取或配置更新。真实安装、返回确认和通知保留，API序列化、UNIX有效负载、三条链路带内和导出尝试分层。宿主CPU/RSS端点和实际迟到均留档，RSS端点不是峰值，也不含采集与发包子进程CPU。

100ms只是计划节拍，年龄界依赖已声明的条件时钟假设，不是硬期限或精确同步。窗口达标也不提供未知供给下的硬预算或硬年龄保证。

## 输出与复核

- `data/observer-training-plan/n4c-static-01/`：业务生成前的训练清单、源码快照、候选、开发协议和消费审计。
- `data/observer-training-validation/n4c-static-01/`：完整测量、逐批观察、两位置年龄界、费用和候选选择。
- `data/observer-training-diagnostics/n4c-static-01/`：选中与旧初始化的双业务采样位置及条件差值。
- `data/observer-training-audit/n4c-static-01/`：独立验收与完整回归。
- `figures/observer-training/n4c-static-01/`：同源TNR 300dpi PNG和嵌入字体PDF。

```bash
python3 -m unittest discover -s tests -v
python3 experiments/check_observer_training_analysis.py \
  --analysis data/observer-training-validation/n4c-static-01
```

重做训练应新建目录。计划绑定首例前源码，源码改变后需要重新prepare。下面只生成已消费11/29训练，不生成新的独立业务：

```bash
python3 experiments/prepare_observer_training.py \
  --output data/observer-training-plan/n4c-recheck-01
python3 experiments/run_observer_training.py \
  --plan data/observer-training-plan/n4c-recheck-01/training-plan.json \
  --output data/network-entry-runs/n4c-recheck-01 --validate-only
sudo python3 experiments/run_observer_training.py \
  --plan data/observer-training-plan/n4c-recheck-01/training-plan.json \
  --output data/network-entry-runs/n4c-recheck-01
python3 experiments/analyze_observer_training.py \
  --run data/network-entry-runs/n4c-recheck-01 \
  --output data/observer-training-validation/n4c-recheck-01
```

网络矩阵期间暂停同宿主回归、离线分析和第二组网络测量。完整矩阵结束后才做排序和前沿分析，所有失败保留，不以重跑替代不利案例。

## 本次交付

完整192例工程通过，30720包、18735报告、1920决策。19200报告槽完成19161，39槽跳过，0个决策跳过。168例条件平均窗口within，24例超限完整保留。最终410项单元检查、851248项网络/观察核对、140232项分析及1362项选择核对通过。

训练暂选stable C07、shared_priority C02、switch C05、bursty_jitter C11，均待重复确认。新旧8配对有5个采样位置相同、6个控制器年龄条件界重叠。旧重点29/C03有13个观察槽跳过，最大发送迟到160.992660ms；不能据单次训练选择宣布阈值收益。全体观察上界最大1083.741667ms，100ms仅为计划节拍。

数据图包括完整96个评分的六层费用前沿投影和训练前后年龄条件界，均为TNR 300dpi PNG与嵌入字体PDF。预算失败的非支配点没有删除，导出单轴也不代表全部资源支配。后续只对预设旧配置、选中和挑战者进行有界重复，再冻结共同初始化及校准动态参数。

完整复核也可执行：

```bash
python3 experiments/diagnose_observer_training.py \
  --analysis data/observer-training-validation/n4c-static-01 \
  --checks data/observer-training-audit/n4c-static-01/independent-checks.json \
  --output data/observer-training-diagnostics/n4c-recheck-01
python3 experiments/validate_observer_training_delivery.py \
  --analysis data/observer-training-validation/n4c-static-01 \
  --figures figures/observer-training/n4c-static-01 \
  --diagnostics data/observer-training-diagnostics/n4c-static-01 \
  --checks data/observer-training-audit/n4c-static-01/independent-checks.json \
  --unit-log data/observer-training-audit/n4c-static-01/final-unit-tests.txt \
  --output data/observer-training-audit/n4c-recheck-01
```
