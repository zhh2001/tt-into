# 固定合流网络入口

通用入口使用双入口分支和公共下游链。`network_spec.py`显式声明采样节点、独立sink、每流路径、FlowTag/Session、寄存器槽、源发送接口和源计数位置。配置工厂支持3至8个采样节点、2至8条并发流，数据面仍为v6、K=1、队列0和256个流槽。

源计数按流的真实入口节点和槽读取。例如五节点四流的四个位置为`(1,1)`、`(2,7)`、`(1,42)`、`(2,250)`。共享状态按节点任务评价，增加载体流不会增加同一个节点的收益分母。

报告读取周期为100ms，编排及付费源计数为1s。合法策略输入仅含实收报告、已确认配置、管理路径、实收需求及实际付费计数。发送日志和离线包编号用于验收与年龄区间重建。

## 已完成的正确性试点

`data/network-scale-validation/n5a-pilot-01/`保存三节点两流、五节点四流各四类案例：静态、phase_joint、phase_guard和许可脚本。脚本验证关闭、恢复、阈值更新、旧会话、旧报告及路径不符探针。

8例全部通过，共1926个业务包、1854份报告、10198个输出帧、180次付费源计数和39次成功字段更新。800个报告观察槽、80个决策周期完整执行。执行前540项单元回归通过，补充门控时钟和起点扫描边界检查后最终543项通过。

费用按每条采样出口带内字节、导出尝试、API请求/回复序列化字节和本地通知有效负载分列。三节点有6层，五节点有8层。试点采用宽松平均窗口限额，8例全部达标。五节点静态案例的节点4/5没有新报告，仍保留全部活跃任务和未知年龄分母。两联合策略实际观察到全部五节点。

这些案例用于通用入口正确性验收。五节点的统一150ms静态配置尚未经性能训练，试点不提供方法排名。八节点/sink9及八流真实入口已完成六方法烟测，见[最大配置正确性与训练准备](README-scale-preflight.md)。

## 复现

从当前源码创建独立新编号的准备计划。计划包含执行前源码摘要，冻结后改变源码会使入口校验失败。

```bash
python3 experiments/scalable_contract.py --output data/network-scale-plan/n5a-reproduction-01
python3 experiments/run_scalable_network.py --plan data/network-scale-plan/n5a-reproduction-01/stage-plan.json --output data/network-entry-runs/n5a-reproduction-01 --validate-only
sudo python3 experiments/run_scalable_network.py --plan data/network-scale-plan/n5a-reproduction-01/stage-plan.json --output data/network-entry-runs/n5a-reproduction-01
python3 experiments/analyze_scalable_network.py --run data/network-entry-runs/n5a-reproduction-01 --output data/network-scale-validation/n5a-reproduction-01
python3 experiments/check_scalable_delivery.py --analysis data/network-scale-validation/n5a-reproduction-01
python3 experiments/plot_scalable_network.py --analysis data/network-scale-validation/n5a-reproduction-01 --output figures/network-scale/n5a-reproduction-01
```

运行依赖本机P4/BMv2/Mininet环境和已核对摘要的项目steady_clock交换机。网络业务期间保持同宿主独立工作负载。每个案例归档实际收发、全部观察前缀、计数与确认、控制账本、诊断读、墙钟条件区间和CPU/RSS端点。入口只清理本次创建的交换机与链路。

## 文献方法的规模入口

`scalable_literature_live.py`与`scalable_literature.py`保留INTOPP-TT和D-INTO-TT的原收益、期限和稳定性目标，并限制候选评价为48次、起点生成扫描为192项、字段写为6次。唯一载体起点按节点生成，避免遍历全部二进制许可向量。

共同初始化可能在一个节点启用多个流。若一次去重需要超过6次许可写，入口分周期修复，已确认变化才结算D-INTO虚拟队列。六方法离线兼容与两周期去重已验收，最大八节点八流入口已完成真实文献方法正确性烟测，性能比较将在静态规模训练后开展。已有三节点文献实现和历史数据保持完整。

## 图和检查

正式诊断图在`figures/network-scale/n5a-pilot-01/`，PNG为300dpi，PDF嵌入Times New Roman，两格式由相同汇总生成。图展示付费计数次数及报告到共享缓存的最大观察延迟，后者包含实际求解对共同循环的阻塞。

独立分析完成61518项核对，公开交付另通过59235项重建核对。`source-manifest.json`绑定实际执行源码，`analysis-source/`单独保留最终分析源码。最终门控有效位和入口时间边界分开核对，报告的出口生成时间不等同于门控寄存器中的入口时间。普通版未启用内部门控跟踪。
