# 初始化、阈值裕量和容量搭载的真实执行

本组将已有 TT-INT 逐流参数部署到固定分支 BMv2/Mininet，检查窗口前启用、成功门控、K=2 搭载、实际报告年龄及控制费用。不是新的在线策略或统计性能比较。

## 输入与边界

使用 `data/admission-reference/flow-indices-03/results.json` 的点 10 和点 37，分别对应 K=1 和 K=2。三条业务路径、120 B 包、800 ms 预热、2400 ms 测量和两个需求阶段均保持原定义。每次 80 个业务包，随机种子为 null，属于确定性正确性检查。

| 输入点 | 容量 | 分支链路上限，各自 B | 共同链路上限，B | 导出上限，B |
| --- | ---: | ---: | ---: | ---: |
| 10 | 1 | 1024 | 960 | 3600 |
| 37 | 2 | 1024 | 960 | 2400 |

P4 沿用独立 `strong_int.p4` 前缀格式，头 4 B、记录 12 B。所有业务包仍封装，容量 0 仅关闭插入。队列快照和生成时间保持相同字段。普通版不加入诊断寄存器。

初始预装早期阈值、容量全为 0。源时间 615、690、740 ms 启用相应逐流容量，1980 ms 下发晚期参数，保留 last_timestamp 与 has_sample。空隙中的发包屏障由目的端确认全部此前业务已经到达。实际调用完成与下一业务包发送时间独立核对。顺序寄存器写入没有跨交换机原子性，未知未来流量不能免费获得这些空隙。

后续裕量变体固定将 100001、200001、500001 us 改为 150000、250000、550000 us。它使用 100 ms 周期相邻倍数之间的中点，阈值 1 与关闭状态保持不变。单元检查验证原理想轨迹中逐包选择及费用相同。它是原有 TT-INT 参数调优。

## 复现

使用新的目录，脚本拒绝覆盖原始或派生产物。隔离单调时钟 BMv2 构建见 `README-clock-budget.md`。

```bash
python3 -m unittest discover -s tests -q
sudo python3 experiments/run_admission_validation.py \
  --output data/validation-runs/admission-diag-new
sudo python3 experiments/run_admission_validation.py --ordinary \
  --output data/validation-runs/admission-ordinary-new
sudo python3 experiments/run_admission_validation.py --threshold-margin-us 50000 \
  --output data/validation-runs/admission-margin-diag-new
sudo python3 experiments/run_admission_validation.py --ordinary --threshold-margin-us 50000 \
  --output data/validation-runs/admission-margin-ordinary-new
python3 experiments/analyze_admission_validation.py \
  --run data/validation-runs/admission-diag-new \
  --output data/admission-validation/admission-diag-new
```

其余三个目录分别运行相同分析命令。每次运行归档实际源码，记录编译 JSON 和交换机二进制的摘要、软件版本、完整配置、时长及开始/结束宿主负载。公开数据保留原包、业务与报告 PCAP、收发事件、链路字段、控制计数和实际源码。原始 RPC 载荷及进程日志只保留在原始运行目录。

公开来源和实际寄存器目标可再次独立核对：

```bash
python3 experiments/check_admission_deployment.py --runs \
  data/admission-validation/admission-diag-01 \
  data/admission-validation/admission-ordinary-01 \
  data/admission-validation/admission-margin-diag-01 \
  data/admission-validation/admission-margin-ordinary-01 \
  --output data/admission-validation/audit-new
```

宿主资源快照为 `data/admission-validation-host-01.json`，逐次运行负载和软件版本保存在各自 summary 中。

## 当前执行结果

四次运行共 8 个配置检查，交付 640 包，独立核对 2140 个输出 IPv4 帧。8 个执行检查均通过，意味着转发、格式、物理资格及所测门控正确，不等于全部配置履约。

| 运行模式 | 点 | 分支 / 分支 / 共同链路 B | 补齐最小帧后的导出 B | 两阶段全任务履约 |
| --- | ---: | --- | ---: | --- |
| 原参数，诊断 | 10 | 308 / 308 / 908 | 2940 | 是 |
| 原参数，诊断 | 37 | 308 / 308 / 884 | 2298 | 否，共同出口晚阶段违约 |
| 原参数，普通 | 10 | 308 / 308 / 956 | 3180 | 是 |
| 原参数，普通 | 37 | 308 / 308 / 884 | 2298 | 是 |
| 间隔裕量，诊断 | 10 | 308 / 308 / 908 | 2940 | 是 |
| 间隔裕量，诊断 | 37 | 308 / 308 / 860 | 2178 | 是 |
| 间隔裕量，普通 | 10 | 308 / 308 / 908 | 2940 | 是 |
| 间隔裕量，普通 | 37 | 308 / 308 / 860 | 2178 | 是 |

原参数诊断点 37 的共同出口晚阶段违约时间比例界为 14.952%–15.680%。这是同一宿主“发送前不晚于生成、生成不晚于采集 recvmsg 完成”的因果区间，不是置信区间或直接相减交换机时钟的年龄真值。原参数普通版的单次履约不能证明可靠性，两种运行均保留，不选择性删除。

裕量变体在普通版和诊断版均保留原理想选择费用。两点启用和阶段更新各为 10、8 次 RPC，请求加回复序列化费用为 1210、972 B。每次运行的两点合计安装计量为 138 次 RPC、17655 B，诊断寄存器重置及读取按语义单独计入 validation。拓扑初始 CLI、TCP 握手、ACK、关闭等全量帧从捕获启动起另行计数，不用序列化字节叠加 TCP 帧。

普通原参数运行全量 loopback 控制帧为 716955 B，普通裕量运行为 716757 B。诊断运行分别为 2088455 B、2080139 B。这里的帧归因是整次运行、四节点双向，不按接收时间强行分摊到每个 RPC 或单个配置。控制费用不能与分时隙配置变更项数等同，也不能推导硬件部署费用。

两点均在原数据预算内。原接纳资源网格没有纳入真实控制预算，本次另设每节点每方向 8 MiB 的宽松正确性上限，不称为原 56 点完整预算可部署。当前只执行这两个点，没有证明其余 54 点的网络可行性，也没有随机重复、独立测试流量或新的编排收益。

## 图片

```bash
python3 experiments/plot_admission_validation.py --runs \
  data/admission-validation/admission-diag-01 \
  data/admission-validation/admission-ordinary-01 \
  data/admission-validation/admission-margin-diag-01 \
  data/admission-validation/admission-margin-ordinary-01 \
  --output figures/admission-validation-new
```

正式图片在 `figures/admission-validation/threshold-margin-and-control.png` 与同名 PDF。图左使用共同出口晚阶段的连续年龄因果界，图右展示启用和阶段更新序列化费用，安装及 TCP 帧在完整账本中另计。两格式来自同一测量脚本，PNG 为 300 dpi，PDF 嵌入真正的 Times New Roman 字体。
