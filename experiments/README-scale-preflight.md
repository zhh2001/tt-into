# 最大配置正确性与有限规模训练准备

八采样节点、八流、独立sink9包含56个许可/阈值配置位置，节点1/2为业务入口，报告按FlowTag/Session关联。运行六方法static、feedback、phase_joint、phase_guard、INTOPP-TT、D-INTO-TT，共同使用100ms报告观察、1s决策及付费源计数、K=1、48候选评价和6字段写上限。

`data/network-scale-validation/n5b-preflight-02`的六例全部通过，3840包、3478报告、34198输出帧、110次成功字段更新、400次付费计数，11层宽松平均窗口费用全部达标。两联合及两文献方法实际观察到八节点，文献方法各48次许可写完成共同初始重复许可的分周期去重。独立检查不调用策略，重建逐包预测、共享收益、虚拟队列及有界候选排序。

600观察槽中483完成、117跳过，60个一秒决策周期完整执行。最大单次求解约482ms，最大报告至共享缓存观察延迟上界约626ms，真实跳过槽及可用时间均保留。该统一150ms初始配置用于最大入口正确性，不提供性能排名。正式启动555项回归、最终563项检查通过，分析189626项及公开重建184996项通过。

## 复现

先从当前源码准备独立新编号清单。目录名应简短，入口在启动前核对BMv2及需求通知套接字绝对路径小于108字节。冻结后不修改执行源码。

```bash
python3 experiments/scale_preflight_contract.py --output data/network-scale-plan/n5b-repro-01
python3 experiments/run_scale_preflight.py --plan data/network-scale-plan/n5b-repro-01/stage-plan.json --output data/network-entry-runs/n5b-repro-01 --validate-only
sudo python3 experiments/run_scale_preflight.py --plan data/network-scale-plan/n5b-repro-01/stage-plan.json --output data/network-entry-runs/n5b-repro-01
python3 experiments/analyze_scale_preflight.py --run data/network-entry-runs/n5b-repro-01 --output data/network-scale-validation/n5b-repro-01
python3 experiments/check_scale_preflight_delivery.py --analysis data/network-scale-validation/n5b-repro-01
python3 experiments/plot_scale_preflight.py --analysis data/network-scale-validation/n5b-repro-01 --output figures/network-scale/n5b-repro-01
```

主业务期间保持宿主独立工作负载，不并发回归、分析、绘图或第二网络。数据归档实际执行源码、控制调用、观察前缀、配置确认、链路PCAP、实际发送和接收及最终诊断。公开图同源导出真正Times New Roman的300dpi PNG与嵌入字体PDF。

## 有限规模训练输入

`data/network-scale-plan/n5b-static-training-01`冻结五拓扑：3/2、5/2、8/2、5/4、5/8节点/流。各48个相同场景共享候选，稳定与共享重点两个场景，复用11/29训练业务，共960个计划案例。候选含均匀阈值、全禁用、单载体旋转、交错阈值、原静态训练配置延拓和固定随机网格补齐，完整配置及去重来源保存。

业务每流8pps、两个5s区段独立初相位，扩流保持已有流包时序。任务按节点共享，普通/末节点稳定目标300/150ms及权重1/2，重点目标500/100ms及权重1/4。评价窗口2–10s，主指标为控制器加权截断生成年龄。

F流时入口带内预算120F、公共链路195F、导出450F B/s，API1200、本地通知128 B/s。基础头最低费用入口48F、公共链路96F B/s，禁用许可仍带基础头。带内与导出随载体数等比例调整，记录绝对费用；固定API预算反映扩流的付费计数和更新负担。

每场景候选须两个训练业务均工程通过及实际窗口预算达标，依次按控制器损失上界最坏值、上界均值、实际导出均值和配置词典编码选择。有限候选不表示全网格最优。训练后进行部署确认再冻结共同初始化。

该prepared计划明确`execution_ready=false`，训练业务尚未生成。下一步补训练执行适配器与单区间任务检查，再独立冻结执行源码，维持本次候选、业务、预算和顺序。新的正式测试业务在静态训练及确认后审计和冻结。

可以从当前源码生成同规则的新输入快照：

```bash
python3 experiments/scale_training_contract.py --output data/network-scale-plan/n5b-training-repro-01
```
