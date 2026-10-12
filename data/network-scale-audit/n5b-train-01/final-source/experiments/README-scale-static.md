# 有限规模静态训练与候选确认

五种双入口合流链拓扑分别为3节点2流、5节点2流、8节点2流、5节点4流和5节点8流。每拓扑48个静态许可/阈值候选，稳定与共享重点两个场景，使用既有训练业务11/29，共960例。每采样节点一个共享任务，增加搭载流不增加任务分母。

`data/network-scale-validation/n5b-train-01`保存全部960例测量、实际执行源码、编译数据面和独立分析源码。276480包完整交付，188044报告，1601164输出帧。567例条件平均窗口预算达标，391例导出超限，2例因墙钟偏移交集不相容而无法评价帧窗口费用。所有案例与空费用值均保留。

独立分析7472646项与公开交付重建7384868项通过。网络启动前576项回归，费用空值处理修正后578项最终回归通过，日志分别归档。95963/96000报告观察槽完成，37槽跳过，9600个整秒决策均完整执行。静态没有在线源计数读取或配置更新。

## 配置、费用和选择

每流8pps，两个5s区段各有独立初相位。扩流保持原有流的包时序。评价窗口2–10s，共同报告观察100ms，决策1s，K=1。格式含12B基础头和12B记录。

F流时入口带内预算120F、公共链路195F、导出450F B/s，API固定1200、本地通知128 B/s。入口基础头最低48F、公共链路96F B/s，禁用仍保留基础头。逐链路、导出帧、API序列化和本地通知分别计费。初始安装、冷复位及通知完整保留在账本，静态测量窗口内API和通知费用为零。

两个训练业务均工程通过且窗口费用within的候选，依次按最坏控制器损失上界、上界均值、实际导出均值和配置编码排序。每单元前两个候选供重复确认。全部480个双业务评分与十个单元的选择都已独立重建。

| 节点/流 | 稳定首选/次选 | 重点首选/次选 | 稳定/重点共同可行数 |
| --- | --- | --- | --- |
| 3/2 | C21/C23 | C22/C21 | 37/38 |
| 5/2 | C15/C12 | C15/C05 | 29/29 |
| 8/2 | C41/C28 | C41/C07 | 11/11 |
| 5/4 | C18/C37 | C29/C37 | 33/33 |
| 5/8 | C38/C47 | C38/C42 | 31/31 |

完整向量及来源见`candidate-scores.json`，损失、费用和软件诊断见`data/network-scale-diagnostics/n5b-train-01`。这里的选择来自每拓扑48个有限候选和训练业务。控制器损失包含未知任务，条件年龄界与统计置信区间分别处理。

## 分批执行与复现

原冻结的960例顺序保持不变，按每拓扑四个48例批次运行。每批独立部署和控制捕获，每例冷复位。IPv4 ID只在批内用于抓包验证，编号从1000j开始，避免跨批编号溢出；合法报告身份仍为FlowTag/Session。

从当前源码准备新的短编号清单，冻结后保持执行源码不变。历史执行清单绑定的是实际运行版本，源码修订后需要新清单。

```bash
python3 experiments/scale_static_execution.py --mode training --output data/network-scale-plan/n5b-repro-01
python3 experiments/run_scale_static.py --plan data/network-scale-plan/n5b-repro-01/stage-plan.json --output data/network-entry-runs/n5b-repro-01 --validate-only
sudo python3 experiments/run_scale_static.py --plan data/network-scale-plan/n5b-repro-01/stage-plan.json --output data/network-entry-runs/n5b-repro-01
python3 experiments/analyze_scale_static.py --run data/network-entry-runs/n5b-repro-01 --output data/network-scale-validation/n5b-repro-01
python3 experiments/check_scale_static_delivery.py --analysis data/network-scale-validation/n5b-repro-01
python3 experiments/summarize_scale_static.py --analysis data/network-scale-validation/n5b-repro-01 --output data/network-scale-diagnostics/n5b-repro-01
python3 experiments/plot_scale_static.py --analysis data/network-scale-validation/n5b-repro-01 --output figures/network-scale/n5b-repro-01
```

运行前核对通知和BMv2套接字路径长度。主业务期间保持宿主独立工作负载，不并发回归、分析、绘图或第二网络。初始回归在建链前执行，逐例检查属于运行验收。宿主资源和实际观察可用时间随案例保存。

正式图在`figures/network-scale/n5b-train-02`，同源真正Times New Roman、300dpi PNG及嵌入字体PDF。十个面板统一纵轴，预算失败保留标记，缺少费用坐标的候选以数量标注，不填充为零。

## 重复确认输入

`data/network-scale-plan/n5b-confirmation-01`冻结20个候选、11/29业务、三个部署块，共120例。顺序种子52603，每块各拓扑独立部署，每拓扑八例。该清单为`shortlist_prepared`且`execution_ready=false`，确认执行入口需另行验收和冻结。

确认后，每候选六例须全部工程及预算达标，再按最坏控制器上界、均值、导出均值和配置排序冻结共同初始化。某单元没有符合条件的候选时保留完整记录，该单元不冻结。名单可从公开训练数据重新生成：

```bash
python3 experiments/prepare_scale_confirmation.py --analysis data/network-scale-validation/n5b-train-01 --output data/network-scale-plan/n5b-confirmation-repro-01
```
