# 独立业务迁移比较

执行 [完整比较清单](../data/network-comparison-plan/n3h-execution-02/comparison-plan.json)，方法依据 [冻结合同](../data/network-comparison-plan/n3h-methods-01/method-contract.json)。八方法、四场景、静态共同初始化、三修复方法全局 P03/P02/P04、字段、K=1、6561 共同配置集合、48 候选和 6 字段写上限保持一致，不根据独立结果重新调参。

历史审计扫描本地 data 的清单、已冻结输入和已保存压缩业务/回放。纯 reserved 与实际已生成/执行分列，历史未知执行声明保守排除。按固定规则选择 3001/3002/3003/3004。每业务两个完整部署块，每块四场景八方法 32 单元，总计 256 例。独立业务样本数为四，两个块只估计同业务部署变异。排序种子 42426407 不增加业务样本。

每例 10 秒，2 秒预热，测量 [2,10) 秒，0/5 秒实际发送需求通知。任务真实生效不随通知迟到后移。在线只有实取合法完整前缀、付费返回计数、确认历史、实收通知及自有完成费用。未来业务、发送仪表和包序号只作离线核对。实际通知按本轮字节收费，不固定为训练的 192 B 或校准的 190 B。

新入口明确 `final_frozen` 及方法摘要，不把模型冻结标记或校准候选索引当正式选择。先验证清单及当前源码，业务生成调用前落盘 `generation-attempts.json`，生成异常也保守视为已消费。工程失败保留并停止，预算失败继续执行和保留，所有产物使用新目录。

```bash
python3 experiments/prepare_network_comparison_execution.py --output data/network-comparison-plan/<new-plan-id>
python3 experiments/run_network_comparison.py --validate-only --plan data/network-comparison-plan/<new-plan-id>/comparison-plan.json --output /tmp/unused-network-comparison-validation
sudo python3 experiments/run_network_comparison.py --plan data/network-comparison-plan/<new-plan-id>/comparison-plan.json --output data/network-entry-runs/<new-run-id>
python3 experiments/check_network_comparison.py --run data/network-entry-runs/<new-run-id>
python3 experiments/analyze_network_comparison.py --run data/network-entry-runs/<new-run-id> --output data/network-comparison-validation/<new-analysis-id>
python3 experiments/check_network_comparison_analysis.py --analysis data/network-comparison-validation/<new-analysis-id>
python3 experiments/plot_network_comparison.py --analysis data/network-comparison-validation/<new-analysis-id> --output figures/network-comparison/<new-figure-id>
```

`--validate-only` 核对完整合同、256 身份和源码，不生成业务，也不启动网络。已消费清单不能换路径重复调用准备入口后仍当独立未见业务。首份准备快照 01 在网络前发现审计将 solver 时间位置误作种子，仅本地保留；最终清单 02 修正该边界并通过回归，没有产生新业务后再改清单。

主指标为所有活跃任务加权的连续采集端生成 AoI 截断归一损失，控制器视图年龄单独给出。费用分为三链路带内、导出尝试、API 序列化字节和 UNIX 通知有效负载六层，捕获管理帧另列。预算达标采用预声明测量窗口条件费用上界，不宣称硬逐窗口监管。

`paired-cases.json` 保留全部 224 个动态/静态同业务、同部署块配对，明确双方预算资格。`business-pairs.json` 保留 112 个业务配对组，两块汇总后再作业务间比较。`deployment-repeats.json` 保留 128 个方法/业务重复组，核对实际采样位置与条件区间。`paired-groups.json` 给出四场景七动态方法的 28 个单元，独立业务分母仍为四。减少导出或修复静态超限不直接算年龄改善。

四业务仅支持有限迁移方向观察，条件差值不是置信区间，区间重叠记为未分辨，不用每包、节点或秒构造独立样本。保留全部无收益、退化、超限和不可评价案例，导出或控制费用增加随年龄变化一起呈现。

主网络期间不并发同宿主回归、离线分析或第二网络测量。控制器及四个 BMv2 的 CPU/RSS 端点与负载逐例保存。网络结束后才进行最终回归、独立分析和绘图。软件环境指标不能推为硬件性能，兼容的时钟条件交集也不是硬同步。

图片保留全部配对、预算失败和两个观察位置，使用真正 Times New Roman 的 300 dpi PNG 和嵌入字体 PDF，数据、脚本和字体摘要同图保存。

## 保留结果

[n3h-comparison-01](../data/network-comparison-validation/n3h-comparison-01/manifest.json) 完成256/256工程核对，40960包、28055报告、150935输出帧、2560决策、1318成功字段更新，无跳过周期。启动和最终均333项单元检查通过，新增18项。独立线格式/配置/控制955163项、可用性49880项、缓存/预算来源12800项、两观察位置及配对汇总141464项通过。四个新业务现全部已消费，清单保持生成前prepared快照。

243/256条件窗口达标，13例超限完整保留。静态切换8例，local切换3004两块，joint重点监测3003两块，phase_joint切换3003第一块超限。静态切换e3为301.5 B/s、导出547.5 B/s；local对应322.5/652.5 B/s；phase_joint对应303/555 B/s，低档限额300/540。joint重点监测导出930 B/s，超过900。三保护方法各32/32达标。费用未知和预算失败不算零，不择优重跑。

全部224配对中166个双方达标。重点监测高档phase_guard P02在四业务、各两块的8个采集端配对上均有严格较低条件差值且双方达标。平均损失界[0.605291,0.610392]，静态[0.628477,0.634097]，但导出均值875.625 B/s对838.125 B/s，API均值304 B/s对0。phase_joint的重点平均损失更低，但仅3/4业务两块均改善，不能只据方向一致性宣布最终赢家或一般最优。

**同一重点phase_guard配对的控制器视图7/8严格更旧，1/8区间重叠。** 当前正向证据仅限于采集端，不能扩写为控制器监测收益。稳定和抖动没有全部四业务两块一致改善。切换静态全部超限，动态方法平均年龄更高，只支持预算与时效取舍。条件差值不是置信区间，四业务不足以作一般显著性结论。静态参照仅经过有限24/6561网络候选挑战，不能声称网络全网格最优或等费用比较。

128个方法/业务重复组中76个采样位置相同，81个条件区间重叠。最大业务内部署中点差约0.432529，来自重点3001/phase_local_guard，两块均保留。最大读取108.438 ms发生在稳定3002第二块phase_joint的0秒预热周期，输入行数0；该案例源迟到最大56.533 ms。根因未确认，未据异常值删除案例。兼容的256例时钟条件交集仍不是硬同步。

正式图保留全部数据，提供 [采集端配对PNG](../figures/network-comparison/n3h-comparison-02/comparison-paired-age.png)、[PDF](../figures/network-comparison/n3h-comparison-02/comparison-paired-age.pdf)，[双观察位置PNG](../figures/network-comparison/n3h-comparison-02/comparison-observers.png)、[PDF](../figures/network-comparison/n3h-comparison-02/comparison-observers.pdf)，[重点观察边界PNG](../figures/network-comparison/n3h-comparison-02/comparison-priority-observation-boundary.png)、[PDF](../figures/network-comparison/n3h-comparison-02/comparison-priority-observation-boundary.pdf)，[六层费用PNG](../figures/network-comparison/n3h-comparison-02/comparison-cost-layers.png)、[PDF](../figures/network-comparison/n3h-comparison-02/comparison-cost-layers.pdf)。均为真实TNR、300dpi PNG与嵌入字体PDF，源摘要随图保留。

下一项先用完整日志核对报告生成、实收、首次进入状态和决策生效之间的可用性链条，保留原参数和原数据。若新增观察周期或策略，另立合同并计入真实增加费用，重新隔离训练、校准和测试，不将已消费3001–3004再称未见业务。

完整观察链诊断已完成，复现方法和边界见[报告观察链说明](README-network-observation.md)。新增诊断保持原年龄、预算和全部案例，下一项进入共同报告观察入口的实际正确性试点。
