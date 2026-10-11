# 共同报告观察下的静态重复确认

本入口采用100ms报告读取和一秒决策。四场景各固定三个静态配置，包括旧共同初始化、控制器目标训练选中配置和最佳其余可行挑战者。使用已消费业务11/29，每个配置/业务三个网络部署块，共72例。

三个块分别启动四个BMv2交换机，前一块客户端和交换机清理完成后启动下一块。块内顺序在准备时冻结。每块PCAP独立归档为`node-N-bB`，报告合法身份沿用版本6，包PID只用于离线关联。

每例10秒，前2秒预热，费用测量窗口为[2,10)秒。所有六次部署的年龄有效且六层条件平均费用均within才可入选。排序依次为最坏控制器年龄损失上界、平均上界、启用项数和配置编码。选中后作为static及全部动态方法的共同初始化。

全部72例与费用失败保留。输出包括两位置年龄、实际报告读取和缓存进入、窗口费用、十二个完整评分、冻结初始化、24个配置/业务重复组和48个候选/旧配置配对。软件宿主资源和迟到随案例保存。固定业务路径、字段和K=1，静态没有在线计数读取或配置写，报告tick也不发起RPC。

## 复现与核对

已有产物位于`data/observer-confirmation-plan/n4c-confirmation-01/`、`data/observer-confirmation-validation/n4c-confirmation-01/`、`data/observer-confirmation-audit/n4c-confirmation-01/`及`figures/observer-confirmation/n4c-confirmation-02/`。

```bash
python3 -m unittest discover -s tests -v
python3 experiments/check_observer_confirmation_analysis.py \
  --analysis data/observer-confirmation-validation/n4c-confirmation-01
```

重做运行使用新目录。准备和validate-only只核对输入，不启动网络或生成业务：

```bash
python3 experiments/prepare_observer_confirmation.py \
  --output data/observer-confirmation-plan/n4c-confirmation-recheck-01
python3 experiments/run_observer_confirmation.py \
  --plan data/observer-confirmation-plan/n4c-confirmation-recheck-01/confirmation-plan.json \
  --output data/network-entry-runs/n4c-confirmation-recheck-01 --validate-only
sudo python3 experiments/run_observer_confirmation.py \
  --plan data/observer-confirmation-plan/n4c-confirmation-recheck-01/confirmation-plan.json \
  --output data/network-entry-runs/n4c-confirmation-recheck-01
python3 experiments/analyze_observer_confirmation.py \
  --run data/network-entry-runs/n4c-confirmation-recheck-01 \
  --output data/observer-confirmation-validation/n4c-confirmation-recheck-01
```

主网络期间暂停同宿主回归、离线分析与第二组网络测量。完整72例结束后再分析和排序。年龄界采用相同时钟条件，部署重复反映软件环境变化。两条业务的重复不会增加独立业务分母。图由数据生成，采用真正Times New Roman，导出同源300dpi PNG与嵌入字体PDF。

## 本次结果

72例工程和预算均通过，11520业务包、8091报告、720决策，7200报告观察槽全部完成。最终429项单元回归及331849项网络、60076项分析、165项选择和843项重复/配对核对通过。

共同初始化冻结为stable C06、shared_priority C03、switch C04、bursty_jitter C11，具体阈值和排序见`frozen-static.json`。24个配置/业务组三块采样位置全部一致。切换业务11中C04相对旧C02在三块均有较低控制器年龄条件界，六层窗口费用相同。

报告到状态更新观察上界最大141.239352ms，首通知首周期可用71/72。实际等待和CPU/RSS端点随数据保留。静态确认已经结束，后续八方法将共同从冻结配置启动，进行全局动态参数校准。
