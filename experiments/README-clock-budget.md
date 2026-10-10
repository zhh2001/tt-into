# 单调时钟与格式、源端和控制成本参照

本组先固定软件交换机计时，再核对成本接口。没有新增 TT-INTO 在线算法，也没有统计性能或硬件实验结论。此前源码和原始测量保持各自版本。

## 独立交换机构建

上游固定为 [behavioral-model 2bdd0b7b](https://github.com/p4lang/behavioral-model/tree/2bdd0b7b2b2ae89faf2720f2158e9842bc6d2dd2)。[补丁](patches/bmv2-steady-clock.patch)将 `SimpleSwitch` 的经过时间和两种队列调度器改为 `std::chrono::steady_clock`，并加入静态断言。P4 ingress/egress 时间戳与 enq/deq 时间差继续按每进程启动后的微秒计，48 位字段和业务代码不变。查询 epoch 的 Thrift 接口仍返回 `system_clock`，没有将日历 API 改成单调 epoch。原实现的时间来源见 [simple_switch.cpp](https://github.com/p4lang/behavioral-model/blob/2bdd0b7b2b2ae89faf2720f2158e9842bc6d2dd2/targets/simple_switch/simple_switch.cpp)，相关字段含义见 [官方说明](https://github.com/p4lang/behavioral-model/blob/main/docs/simple_switch.md)。

本机 GCC 13.3 的类型检查确认 high_resolution_clock 与 system_clock 为同一类型且不是 steady，见 [clock-types.json](../data/clock-validation/clock-types.json)。这只描述本次环境，不能假定所有标准库都作相同映射。

实际构建使用的补丁原文与摘要保留于 [build-patch-01.json](../data/clock-validation/build-patch-01.json)。当前公开补丁使用无上下文差分，避免补丁空白上下文在暂存检查中被判为尾部空格。重新应用后，三份修改源码的摘要均与已测构建一致。

当前构建脚本从固定提交下载源码，所有源码、库、程序和日志放在 `build/` 下，无需 sudo，不安装到全局路径。需要既有 Autotools、C/C++、Thrift、Boost、GMP、libpcap 和 nanomsg 开发依赖。

```bash
python3 experiments/build_steady_bmv2.py --output build/bmv2-steady-new --jobs 2
sudo python3 experiments/run_steady_validation.py \
  --build build/bmv2-steady-new --output data/validation-runs/steady-common-new
sudo python3 experiments/run_steady_validation.py \
  --build build/bmv2-steady-new --format task --ordinary \
  --output data/validation-runs/steady-task-ordinary-new
python3 experiments/analyze_strong.py \
  --run data/validation-runs/steady-common-new \
  --output data/clock-validation/steady-common-new
```

本轮实际构建程序版本为 `1.15.0-2bdd0b7b-steady-unknown`，二进制 SHA-256 为 `dad393c75681d9d043dd5a75f1e0773a0399d8b454b30bae92310e08d58420bf`，补丁及链接库摘要列于各运行的 `runtime_clock.build`。该次执行的[构建脚本快照](../data/clock-validation/build-source-01.py)按哈希保留。上游配置器以 PI 选项是否出现决定启用，首版的 `--without-pi` 实际启用了本机已有 PI。当前脚本省略该参数并修正版本后缀，独立 configure 检查通过，未用修正后的脚本重新全量编译本轮二进制。时钟补丁相同，后续重建程序可能有不同摘要，应按自身清单记录，不声称可逐字节重建该次程序。

## 进程内跳变与证据

常规 common 诊断版和 task 普通版各完成 10 项检查。另用 LD_PRELOAD 只替换指定交换机进程内的 CLOCK_REALTIME 查询，启动 100 ms 后每 200 ms 交替施加 +3600 s 和 -3600 s 偏移。没有调用宿主设置时间的 API，也不把该环境传给发送器、采集器或其他项目程序。

```bash
sudo python3 experiments/run_steady_validation.py \
  --build build/bmv2-steady-new --inject-realtime-jumps \
  --cases shared dev_filter feedback_pro \
  --output data/validation-runs/steady-clock-jump-new
sudo python3 experiments/run_steady_validation.py \
  --build build/bmv2-steady-new --legacy-control --inject-realtime-jumps \
  --cases shared --output data/validation-runs/legacy-clock-control-new
```

`--legacy-control` 使用原安装程序作异常阳性对照，预期出现失败，不能作为正常基线性能运行。它也只在自己的子进程内加载注入库。

| 运行 | 网络检查 | 输入 / 交付包 | 核查输出 IPv4 帧 | 原状态 |
| --- | ---: | ---: | ---: | --- |
| [steady-common-01](../data/clock-validation/steady-common-01/summary.json) | 10 | 2274 / 2274 | 10806 | passed |
| [steady-task-ordinary-01](../data/clock-validation/steady-task-ordinary-01/summary.json) | 10 | 2274 / 2274 | 10797 | passed |
| [steady-clock-jump-01](../data/clock-validation/steady-clock-jump-01/summary.json) | 3 | 276 / 276 | 1184 | passed |
| [legacy 阳性对照](../data/clock-validation/clock-comparison-01/legacy-clock-jump-control-01/original-manifest.json) | 1 | 24 / 2 | 不作正常分析 | failed |

单调版本共 23 项通过，4824 包完整交付，核查 22787 帧。跳变运行四个进程各记录到正、负注入，共 161 条切换记录。常规和跳变的源端诊断及收到的生成时间未出现恒偏移不相容。普通版没有逐包源端诊断，只核对实际生成报告。已有因果年龄继续使用宿主单调域边界，不直接相减不同交换机时间戳。

失败阳性对照原样保留，只有 2 包交付不能写成完整通过。24 个源端到包时间仍读回，其恒偏移区间不相容缺口约 7199.481 s。源端边界使用“发送前 ≤ ingress 事件 ≤ 对应寄存器 RPC 读回后”，即使业务包没有到达也有合法读回上界。分析及[比较摘要](../data/clock-validation/clock-comparison-01/summary.json)没有将原 failed 状态改成 passed。

[时钟图 PNG](../figures/clock-validation/in-process-clock-jump.png)及 [PDF](../figures/clock-validation/in-process-clock-jump.pdf)展示 ingress 经过时间与发送经过时间之差，均以首包为基点。左、右面板分别使用秒、毫秒，这不是年龄、同步误差或精度标定。48 位时间按相邻最短有符号差展开，只用于该短轨迹。测试验证指定实现对本次日历跳变的行为，不证明任意平台、休眠、迁移或负载下的长期计时品质。

## 成本接口与范围

`budget_model.py` 和 `budget_oracle.py` 为独立版本，没有修改原有 8+20 B 事件模型。支持 common/compact/task/prefix 格式，按格式计算 MTU 容量和最小 60 B 导出帧。源端可固定封装或由离线参照选择部分允许包。任务资格、到包时刻、生成时刻、报告可用时刻及丢失均为给定输入。报告导出后丢失仍计导出字节，sink 导出前抑制则不计未发送的导出帧。

离线 MILP 先最大化硬期限接纳权重，再最小化数据成本。控制账本由调用者完整声明，含窗口前安装及观察、更新、标定、验证和未归因费用。它可以独立限制每条控制链路预算，但没有优化观察频率、控制决定、下发延迟或动态资格。账本的时刻不会自动更改包资格，调用方须提供与实际生效一致的轨迹。

源端 optional 与逐包权限均为未来已知的下界选择，不证明能以账本费用部署这些选择。返回字段 `deployment_feasibility_established=false` 明确保留此边界。无替换模型不计蓄水池中途被替换记录的沿途成本，因此真实字节核对只纳入前缀场景。要求源端许可或版本字段时，task/prefix 会被拒绝，不能默认使用最便宜格式。

`MeteredClient` 执行同一 Thrift 调用，记录实际请求和回复序列化长度。普通 task 十项检查的分类为：

| 类别 | 调用数 | 请求 B | 回复 B |
| --- | ---: | ---: | ---: |
| 安装与冷启动重置 | 487 | 44454 | 15065 |
| 在线观察 | 72 | 5796 | 2880 |
| 在线更新 | 45 | 5598 | 1386 |
| 导出丢失注入 | 4 | 364 | 120 |

这些调用共 75663 B 序列化载荷。初始化 CLI、TCP 建连、确认、分段和其他捕获内容另含于 loopback 总量 948196 B。完整帧账本按四节点、双方向共八条逻辑连接分列，无法可靠归因到单个 RPC 的帧标为 unattributed，不用 recvmsg 完成时间强行归因。loopback/GSO 口径仍不能代表硬件控制网络。

控制账本可使用 Ethernet 或 serialized_rpc 口径。同一账本不得混合两层字节，序列化控制账本不会与 Ethernet 数据费用合成一个总值。账本汇总的占位时刻不代表实际下发事件，真正的调用起止时间保留在原 API 记录中。[控制图 PNG](../figures/clock-validation/metered-rpc-cost.png)及 [PDF](../figures/clock-validation/metered-rpc-cost.pdf)只展示上述计量调用。

## 成本检查和后续适用范围

```bash
python3 -m unittest discover -s tests -v
python3 experiments/check_budget_contract.py \
  --runs data/clock-validation/steady-common-01 \
         data/clock-validation/steady-task-ordinary-01 \
  --output data/budget-contract/contract-new
python3 experiments/analyze_clock_validation.py \
  --runs data/validation-runs/steady-common-new \
         data/validation-runs/steady-task-ordinary-new \
         data/validation-runs/steady-clock-jump-new \
         data/validation-runs/legacy-clock-control-new \
  --output data/clock-validation/clock-comparison-new
```

全部单元检查为 46 项。新增十项包含手算、四格式与两种封装模式的穷举、MTU、丢失和元数据需求边界。独立成本核对通过 9 个真实前缀场景，包含共同格式导出前抑制。该核对从首链路恢复实际封装并检查后续字节，不宣称独立预测了全部控制决策。

[contract-03](../data/budget-contract/contract-03/summary.json)另含 40 次合成求解。共同格式下一个小例子的固定封装只能接纳权重 1，可选封装可接纳权重 3，但 compact 等精简格式固定封装已经接纳全部任务。因此该例子不支持新算法收益，反而要求后续对照先核对必要字段。导出延迟或丢失仍可降低可接纳权重，预声明控制费用超限时所有格式都不可行。给定的初始已收样本和手算控制费用是合成假设，不是网络测量。

后续需先检验跨流任务资格及动态需求，给出实际可执行的可行解和充分调参的静态强对照。再分离训练/测试、加入完整控制生效过程与随机重复。当前结果不能锁定 TT-INTO 的贡献或性能结论。
