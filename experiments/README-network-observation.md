# 网络报告观察链诊断

输入为已经完成的[独立业务比较](../data/network-comparison-validation/n3h-comparison-01/manifest.json)，完整清单摘要固定为 `1c8084770ecb68faa73d485489dfed84475c661abb1eb40f3f43b8050e3b292a`。脚本不启动网络，不生成业务，不改变八种方法的配置或预算。四业务3001–3004均已消费，两个部署块用于观察同业务部署变异。

```bash
python3 experiments/diagnose_network_observation.py --analysis data/network-comparison-validation/n3h-comparison-01 --output data/network-observation-diagnostics/<new-id>
python3 experiments/check_network_observation.py --analysis data/network-comparison-validation/n3h-comparison-01 --diagnostic data/network-observation-diagnostics/<new-id> --output /tmp/<new-id>-checks.json
python3 experiments/plot_network_observation.py --diagnostic data/network-observation-diagnostics/<new-id> --checks /tmp/<new-id>-checks.json --output figures/network-observation/<new-id>
python3 experiments/validate_observation_delivery.py --diagnostic data/network-observation-diagnostics/<new-id> --figures figures/network-observation/<new-id> --checks /tmp/<new-id>-checks.json --unit-log /tmp/<full-unit-log> --output data/network-observation-validation/<new-id>
```

输出必须使用新目录，独立验收文件也必须不存在。正式诊断为 [n4a-03](../data/network-observation-diagnostics/n4a-03/manifest.json)，独立验收及单元日志见 [检查附件](../data/network-observation-validation/n4a-03/manifest.json)。开发诊断01/02保留在本地，来源数据和原年龄分析均未修改。

`report-chains.csv` 保留每个报告记录的实际完整前缀、生成条件界、recvmsg完成、状态更新界和首次决策截止。离线包位置只用于对齐验证，不是在线策略输入。字段确认取自真实RPC与确认历史，部分生效区间独立列出，不假定跨节点原子更新。

逐流新报告与共享对象缓存推进分开。`batch_latest=True` 表示该样本成为本次批末缓存中的最新值，批内被新样本覆盖不等于该报告没有其他信息价值。`not_read_in_cycles` 表示报告尚未进入这十个实际决策周期，不表示丢包或事后采集程序没有记录。重复和旧记录不重置缓存，真实任务分母保持完整。

`task-losses.csv`、`case-diagnostics.csv` 保留960个活跃任务区间的连续截断年龄。生成上界与更早可用时刻得到年龄下界，反向组合得到上界。两个观察位置之差称为观察附加损失，包含等待、同批覆盖和尾段状态等影响，不能直接当作缩短轮询周期的因果收益。条件界不是统计置信区间。

`object-batches.csv` 保留7680个对象批次的最新缓存样本及更新时已有年龄。`read-batches.csv` 保留全部2560个读取批次。`send-timing.csv` 保留40960次发送时序，`notice-chains.csv` 保留全部实际通知及首次可用周期。

`paired-observers.json` 保留224个动态/静态配对及逐任务贡献，预算失败资格保持原值。`deployment-divergences.json` 保留128个方法/业务组在通知、合法样本位置、付费计数、选择和已确认配置上的第一分歧。通知分组不是随机干预，不从分组差异推出因果结论。`anomaly-index.json`保留13个预算失败和最大的读取、发送迟到及重复差异索引，完整原表不会按异常筛掉。

独立检查器从实取前缀和原视图重建缓存，从原始生成条件界用分段积分核对所有任务与两观察位置年龄，核对全部配对、贡献分解、重复分母、异常索引与方法均值。来源摘要、执行源码和任务贡献破坏检查同时保留。

本轮256例重建完成，28055条报告记录、25240次共享缓存推进、6724次批末最新更新、18516次批内覆盖、2815条未进入实际周期的尾段记录。完整362项单元检查及761636项独立核对通过，未新增网络运行。

重点监测phase_guard的八个采集端配对仍全部较低，控制器端仍七个较高、一个区间重叠。两方法每例平均批末更新均为27次，平均合法新记录129对125.75。采集端配对差值均值界为[-0.028806,-0.018086]，控制器端[0.019425,0.028080]，观察附加损失差[0.037511,0.056886]，均为原四业务两部署的条件观察。逐任务中点贡献显示，节点3的采集端平均改善没有保留到控制器端，节点1控制器退化也参与总体反转。

128个重复组中53组出现选择配置或理由分歧。其中43组初始通知可用周期相同，不能把全部差异归为初始通知。108.438ms读取和56.533ms发送迟到位于同一稳定场景预热期，根因仍未确认。

正式图为[观察损失分解PNG](../figures/network-observation/n4a-03/priority-observation-loss.png)、[PDF](../figures/network-observation/n4a-03/priority-observation-loss.pdf)，[任务贡献PNG](../figures/network-observation/n4a-03/priority-task-contributions.png)、[PDF](../figures/network-observation/n4a-03/priority-task-contributions.pdf)，[报告处理去向PNG](../figures/network-observation/n4a-03/report-batch-outcomes.png)、[PDF](../figures/network-observation/n4a-03/report-batch-outcomes.pdf)。图片使用真正Times New Roman、300dpi PNG和嵌入字体PDF，源摘要与验收绑定随图保留。

这些结果支持继续做共同观察入口的真实试点。离线提前报告可用时间不能当部署性能，缩短报告观察周期是否能保留编排收益仍须真实测量。改观察入口时，两方法共用相同周期，源计数与配置调用按实际执行收费，训练、校准与新测试继续隔离。
