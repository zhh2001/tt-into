# 共同报告观察入口正确性试点

报告完整前缀每100ms读取一次，编排决策和付费源计数仍按一秒节拍执行。静态TT-INT与冻结的phase_guard P02共用同一个入口。独立入口复用版本6的P4、合法身份校验、逐字段确认和实际调用账本，保留旧秒级入口。

首轮采用已消费训练种子11，覆盖稳定、重点监测、需求切换和抖动突发，共8例。每例10秒，前2秒为预热，费用窗口为[2,10)秒。方法参数、预算、共同初始化、执行顺序及源码摘要均在业务生成前冻结。

## 调度与合法观察

每个观察槽记录实际启动、完整行前缀、文件摘要、读取完成、状态更新时间区间、新取得报告和共享缓存。整槽迟到时保留跳过记录，不能补写虚构观察。决策明确引用截止前最新完成的观察批次。重复、旧生成记录和旧会话不会重置新样本缓存，归档末段也不补在线更新。

报告tick只读本地已收文件，不发起源计数、配置或核对RPC。动态方法每个正常决策之后实际读两个源计数，返回完成后才可用。通知仍在秒级决策中取得。各类API序列化字节、UNIX通知有效负载、链路带内和导出尝试分别记账，文件读取的CPU、RSS端点及迟到也保留。

## 首轮结果

8/8工程通过，1280包、902报告、4742输出帧、80决策，800/800报告观察完成，无观察或决策跳过。895条新报告实际进入缓存，7条未读末段保留在年龄评价中。动态方法共80次付费源计数和13次成功字段更新，静态没有在线计数读或更新。8/8条件平均窗口达标。

报告recvmsg完成至首次缓存更新的观察上界最大101.841ms。读取最大3.598ms，状态更新最大1.423ms，求解最大50.579ms，实际在线RPC最大0.967ms。100ms为预定节拍，不是硬延迟保证。宿主资源区间覆盖安装至核对完成，RSS端点不是峰值，也不包括采集和发包子进程CPU。

启动回归382项，最终回归387项。线格式、配置与费用核对25660项，新观察批次及来源17255项，缓存与预算400项，双位置年龄和汇总6702项。交付绑定及字体检查403项。Times New Roman的300dpi PNG和嵌入字体PDF由同源数据生成。

这里只是一个已消费训练业务、每单元一次的正确性试点。重点监测的控制器年龄条件界较低，同时增加API和导出费用。稳定和突发没有分离的改善界，切换年龄更高。这些结果不构成新业务统计排名，不把公共观察入口改善归为编排贡献，也不宣称等费用提升。

## 文件与复核

- `data/report-observer-plan/n4b-pilot-01/`：业务生成前的prepared计划与冻结方法。
- `data/report-observer-validation/n4b-pilot-01/`：完整八例、PCAP、报告/控制事件、源码快照、两位置年龄界和分层费用。
- `data/report-observer-audit/n4b-pilot-01/`：独立检查、最终回归及交付验证。
- `figures/report-observer/n4b-pilot-01/`：两位置年龄图和报告等待分布。

```bash
python3 -m unittest discover -s tests -v
python3 experiments/check_report_observer_analysis.py \
  --analysis data/report-observer-validation/n4b-pilot-01
```

新的实际运行应使用新目录。已归档计划绑定当时源码，不能在改动后直接沿用它启动网络。以下命令仅重做已消费业务，不产生独立测试：

```bash
python3 experiments/prepare_report_observer.py \
  --output data/report-observer-plan/n4b-recheck-01
python3 experiments/run_report_observer.py \
  --plan data/report-observer-plan/n4b-recheck-01/observer-plan.json \
  --output data/network-entry-runs/n4b-recheck-01 --validate-only
sudo python3 experiments/run_report_observer.py \
  --plan data/report-observer-plan/n4b-recheck-01/observer-plan.json \
  --output data/network-entry-runs/n4b-recheck-01
python3 experiments/analyze_report_observer.py \
  --run data/network-entry-runs/n4b-recheck-01 \
  --output data/report-observer-validation/n4b-recheck-01
```

网络期间应暂停同宿主回归、离线分析和第二组网络测量。分析使用新增观察事件格式，不能把旧十批分析器删掉数量校验后直接套用。
