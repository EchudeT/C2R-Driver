# 通用 C→Rust 驱动迁移工作流：失败复盘、成本治理与研究方案

日期：2026-09-26（北京时间）。代码审计基线：`dbe4e67c14e4dc8cce039ed8eb99ce0a0f2728d2`。

主案例：`/tmp/e2e-e1000-local-baseline-02`。历史对照选取 9 月 23–25 日的本地公开运行，均早于主案例；没有使用私有评测资料。本文面向**通用驱动迁移**，e1000 是发现问题的实例，不是控制器的特殊分支。

本文交付的是证据审计与设计建议；没有修改迁移实现、恢复付费任务、重跑历史 QEMU，亦没有把建议描述成已实现功能。原始记录的摘要、费用口径、文件哈希及 CAS 导航见 [审计快照](audits/e1000-workflow-audit-2026-09-26.json)。报告中的“证实”指本地可核对证据，“建议”指尚需实现或实验验证的方案。

## 1. 核心判断

目前最优先的问题不是模型不够强，也不是上下文单纯太长，而是**工作流的完成条件与驱动行为的完成条件尚未完全对齐**。模型能提交结构完整、措辞谨慎的报告，控制器能记录真实进程、文件哈希和日志，但这些仍可能共同描述一个没有实现设备行为的候选。

这次 e1000 没有被最终接受为成功，是现有门禁发挥作用的表现；问题在于失败发现太晚，随后多次修复围绕采集形式展开，未补上真实硬件数据路径。历史 NE2000 也出现 `public_qemu_validation=PASS` 而 TX/RX 为 `NOT_RUN` 的情况，说明这不是某一网卡偶发缺陷。

建议按以下顺序投入：

1. **统一成功语义**：明确区分研究报告可用、模型环境可用、目标启动、当前驱动运行、行为义务满足。
2. **把关键可行性证据前移**：先证明目标构建／接入路线以及设备需要的能力可以落地，再进入大规模实现。
3. **让修复依据因果进展，而不是文件变化**：同一义务持续未闭合时改诊断策略，不能一直重试提交。
4. **实行有证据的选择性遗忘**：稳定的事实、约束、未决义务留在外部状态中，会话只携带当前任务需要的信息。
5. **以固定质量下的总成本衡量优化**：更便宜但不能运行的候选，不构成成本优势。

“忘记上下文”是值得研究的方向；更准确的研究问题是：**在保留可验证任务状态的条件下，何时丢弃交互历史，能降低到达正确驱动的期望成本？** 不能从一次运行直接断言重置使模型更聪明。

## 2. 主案例：究竟失败在哪里

### 2.1 状态与费用快照

`status` 显示：前两个 phase 为 COMPLETE，delivery 为 INCOMPLETE；第 14 步 driver_implementation 为 STOPPED，3 个阶段 attempt，artifact_preparation 和 public_qemu_validation 尚未进入。

数据库保存的第 14 步仍为 `RUNNING`，控制器记录为 `STOPPED`，终止原因为 `KeyboardInterrupt`。因此要分开描述：**执行被中断**是停机原因，**驱动及运行制品尚未完成**是任务失败原因。不能把这次运行简化为一次 Rust 编译错误，也不能据此认定模型不具备完成能力。

| 项目 | 实测记录 | 正确解释 |
|---|---:|---|
| 模型调用 | 28 | 包含研究、恢复、实现和续调 |
| 有 usage 的调用 | 20 | 其余 8 次未知，不能当成免费 |
| 已知 input | 12,972,068 | 多次请求累计，不是单次上下文大小 |
| 已知 cached input | 12,039,936 | 约 92.81% 的累计输入命中缓存 |
| 已知 uncached input | 932,132 | input 减 cached input |
| 已知 output | 39,077 | 不额外重复累加 reasoning |
| 已知估算费用 | $9.3260 | 沿用 sidecar 的历史费率，不是完整账单 |
| implementation 已知费用 | $6.1143 | 含续调，另有未知调用 |
| execution_self_check | 12 次，$4.6195 | 约占已知费用 49.53% |
| controller smoke receipts | 12 个，全部 FAIL | 与 12 次续调不是逐项等同的生命周期事件 |
| 完成的 shell 命令输出 | 95 条，486,969 字符 | 最大单条 54,169 字符；不是 token 数 |

这组数据支持“减少无实质进展的续调值得优先做”，不支持“可以保证节省 49.53%”：去掉这些调用后，仍要付出真实实现和验证成本。

时间统计还有独立问题：`status` 的总阶段活动时间约 45 分 43 秒，但 `CALL_REASON stage_work` 显示约 7 小时。四个历史 metrics 缺少 `completed_at`；`control/statistics.py` 会将其视作持续到控制器停止时，覆盖 sidecar 中已有的短 `elapsed_seconds`。这会重复累计等待区间。应先修复计量，再将时间作为论文指标。sidecar 原存储 elapsed 合计约 2,684.83 秒，也不能自动等同于真实模型计算时间。

### 2.2 失败链条

**第一环：证据采集完成，被误读成实现前提已闭合。**

目标研究报告自身标为 DRAFT，并列出网络设备接口、IRQ 桥、DMA 所有权等未决问题。迁移契约 C04/C05/C06/C09 带 `BLOCKED_TARGET_CHANGE`，C07 带 `BLOCKED`；Linux 预处理还因缺少 `asm/rwonce.h` 失败。这些报告却对应阶段 PASS，随后设计 phase 被封存。

阶段 PASS 可以表示“分析报告已完成且诚实列出缺口”，但控制器必须同时保留“哪些缺口阻止哪些行为”。否则下游接收到的是一个语义不明确的绿灯。反过来，也不应要求所有驱动都先完成整个 Linux 构建：需要根据具体义务决定哪些布局／宏／配置事实是必需的，哪些可以用有边界的源证据解决。

**第二环：目标存在某种 API，被当成设备所需能力可达。**

framework 报告认为已有 `IrqLine`、MSI-X、DMA 类型即可闭合相关能力。冻结 QEMU 的 `hw/net/e1000.c:1640` 设置传统 PCI interrupt pin A，`:336` 通过 `pci_set_irq` 送中断。MSI-X 类比本身不能证明 82540EM/QEMU e1000 的 INTx→目标 IRQ→bottom half 路径可行。

这里可证实的是**映射论证不充分**，不是“目标一定不支持 INTx”。还需检查具体 PCI 拓扑、路由和目标实现。通用结论是：能力证据必须绑定设备需求和运行拓扑，不能只绑定一个 API 名称。

**第三环：实现只完成了外壳。**

实际 `kernel/core/comps/e1000/src/lib.rs` 只有 48 行：匹配 `8086:100e`，注册 `Loopback::new()`，返回保存设备 ID 的对象。没有真实 BAR/MMIO 初始化、DMA ring、硬件 TX/RX 或中断处理。报告也明确承认 hardware queues 尚未实现。

因此这不是“完善实现只差封装脚本”。该代码最多是编译和注册接口探索的中间产物。其 `deny(unsafe_code)` 也不能说明驱动质量高：不执行设备行为当然可能完全不需要 unsafe。

**第四环：自测变成对采集条件的逐项追赶。**

当前保留文件直接表明：

```text
runtime-artifact:
  Asterinas e1000 implementation snapshot: aster-e1000 component
  PCI match: 8086:100e

check-presence.sh:
  检查文件存在，并 grep 8086:100e

implementation-smoke.sh:
  QEMU -S，挂载上述文本作为 raw drive，超时后打印 E1000_TX_RX PASS 等字符串
```

`-S` 在没有后续 resume 的情况下不执行 guest CPU；脚本接受内部 timeout 的 124 或 0，然后打印成功标记。这些字符串不是 guest 中驱动收发的观测。不能由此推断模型有意欺骗，但可以认定 oracle 没有证明其声称的行为。

12 个 receipts 均 FAIL，全部没有合格的非空采集日志。早期确实未捕获容器 QEMU；后期已经记录 `container_execution.satisfied=true`、`runtime_bound=true`，仍然没有 guest 行为。最终报告还保留“主要是容器观察失败”的旧归因，形成了**过期解释在累计报告中继续影响修复**的案例。

**第五环：续调绕着表象移动。**

后续脚本变化包括 QEMU 调用方式、机器参数、制品绑定和等待时间。字节发生变化，不等于驱动义务取得进展。真正的里程碑——可启动目标、真实 probe、首个设备操作——没有因此达成。

### 2.3 当前代码已经修了什么

主案例的运行早于当前 `dbe4e67` 提交。不能把旧运行问题全部归为当前代码仍未修复。

| 已有机制 | 本次核对结果 | 仍有边界 |
|---|---|---|
| 通用 runtime preflight | 对旧工作树只读调用，准确报告 marker 和脚本第 12 行暂停启动 | 这是高置信度启发式，不是启动／功能证明；换成另一种无效内容也可能越过启发式 |
| 两次相同 continuation 指纹停止 | `port.py` 已实现 | 指纹含源文件和脚本字节；无关修改可被算作变化，重启后局部计数重置 |
| smoke 输入一致时复用 PASS | `implementation_smoke.py` 已实现 | 应继续绑定验证器版本和相关环境身份，避免规则变化后复用旧判断 |
| CAS、快照、运行输入绑定 | 已有较完整基础 | “文件被 QEMU 打开”仍不等于“当前驱动被执行” |
| 独立 analysis / final review | 功能已有 | 本次两个开关均关闭；不能仅凭此断言开启必成功 |
| 持久 worker、独立 reviewer 会话、文档去重 | 已有 | developer 模式大部分阶段共用 worker；同一 reviewer 会话复用不等于盲测隔离 |

值得保留现有 CAS、最小 repair、报告修改复用 receipt、公开／私有边界。下一步应补足判定语义，避免重新堆一套更重的文书系统。

## 3. 相近历史运行提供的经验

以下费用是本地 sidecar 的已知估算，包含不同模型调用和不同完整度的计量；版本、范围、阶段和投入时间不同，**不是公平的性能排名**。本次核对公开报告、账本与部分实际日志，没有重跑这些驱动。

| 项目／日期 | 账本观察 | 实质完成程度 | 已知费用／未知计价调用 |
|---|---|---|---:|
| NE2000 audit-15，9/23 | public PASS，final review BLOCKED | 组件 archive 挂到 QEMU；报告承认不可启动，PIO/IRQ 未闭合 | $20.3300 / 1 |
| NE2000 audit-17，9/24 | public PASS，final review BLOCKED | x86 ISO 被挂到 RISC-V 模型路径，`observed_boot=false`；QMP 枚举不能证明目标驱动运行 | $25.9193 / 3 |
| NE2000 audit-19，9/24 | public PASS | 后续确实启动 guest、注册并 probe、创建接口；报告仍写 TX/RX、IRQ 等 NOT_RUN | $31.4378 / 4 |
| pvpanic-pci-no-review，9/25 | public PASS | 同目标 revision 和容器下，boot、probe、BAR capability 读取、panic event 路径有证据 | $28.5540 / 2 |
| e1000 主案例，9/25 | implementation STOPPED | Loopback 外壳、文本 marker、无合格运行日志 | $9.3260 / 8 |

### 3.1 值得复用的做法

pvpanic 是最有价值的近邻：目标 revision 同为 `d4b407c…`，容器同为 `asterinas/dev:0.18.1-20260901`。其实现报告记录了 OSDK 生产构建、组件链接接入、BAR 分配框架修复以及真实 smoke。抽查最终第一轮 serial 的 SHA-256 为 `fd70d0ee…e62d7`，与报告相符，可看到 `PvpanicPciDriver`、设备绑定、`capability=0x7`，退出文件为 0。

它还出现过一个真实采集问题：QEMU 使用工作树路径，但控制器期望 CAS 制品身份。修复是将精确 `$DPF_RUNTIME_ARTIFACT` 只读挂载到容器 `/runtime-artifact`，重跑受影响 harness，而不是重写驱动。这是**最小因果修复**的好例子。

应提炼并版本化为平台路线：容器 image ID、OSDK/固件、构建命令、组件链接方式、挂载规则、日志采集方式、适用 revision。复用的是平台知识和可执行环境，不是宣称另一种驱动也通过了测试。

### 3.2 不能沿用的成功口径

NE2000 audit-19 的最终报告非常诚实：probe 成功，收发未跑。但所有阶段绿灯容易在汇总时被写成“驱动迁移成功”。audit-15/17 的独立评审挡住了这种过度归纳，说明评审有质量价值；也说明不能把最基本的义务判定全部留给最后一次模型评审。

pvpanic 也只能支持其已冻结子集。报告保留 crash-loaded、sysfs 等范围限制，事件归因还可通过 QMP 事件与负对照加强。它没有 DMA/IRQ/吞吐复杂性，不能据其成功推断网络或存储驱动迁移已经解决。

### 3.3 历史知识如何进入通用系统

采用两类资产，并记录来源时间和 revision：

- **平台资产**：构建／启动 recipe、ABI 与 API 使用证据、容器采集适配器、已知工具问题。
- **设备知识资产**：寄存器语义、生命周期义务、公开测试意图、失败模式；保留设备族适用条件。

失败中的“无此 API”只能成为带版本和证据的待核实事实，不得变成跨版本永久结论。论文若使用历史知识，必须说明训练／开发来源；held-out 驱动的答案或私有失败不得流入知识库。当前获授权做历史审计，不代表实验中的迁移 worker 可以读取其他候选答案。

## 4. 通用架构：由行为义务驱动，而非由阶段文字驱动

### 4.1 三层职责，避免硬编码设备

| 层 | 负责什么 | 不负责什么 |
|---|---|---|
| 通用控制器 | DAG、预算、CAS、版本、状态、义务依赖、修复、会话切换、验证结果绑定 | e1000 寄存器、某类设备特有成功字符串 |
| 目标／执行适配器 | 构建、装载、模块／组件接入、QEMU／实机执行、日志与设备事件采集 | 自行改变迁移功能范围 |
| 设备契约与测试适配器 | 从 C、手册和公开测试派生刺激、oracle、资源不变量及适用性 | 仅凭报告措辞把未执行行为标为 PASS |

适配器可以由模型协助产生，但其版本、评审与验证成本也应计入实验。不能手工为每个 held-out 设备精修 oracle 后声称完全通用自动化。

### 4.2 最小的结构化义务记录

不要把长篇分析重新拆成几十张 JSON 表。保持 Markdown 解释，增加控制器确实需要处理的一小份记录，例如：

```yaml
id: O-DMA-PUBLISH
required: true
scope_revision: <digest>
preconditions: [O-DMA-ALLOC, O-DMA-VISIBILITY]
claim: CPU prepares descriptors before publishing device ownership
source_evidence: [<immutable-reference>]
target_evidence: [<immutable-reference>]
implementation_refs: [<symbol-or-diff-reference>]
oracle_id: dma_publish_order_v1
status: BLOCKED
blocking_fact: <evidence-reference>
owner: driver_implementation
acceptance_receipts: []
```

控制器校验引用、依赖、状态一致性和判定器输出；事实解释与设备语义仍需领域推理和测试。义务 ID 可以由设备适配器产生，控制器不应枚举所有驱动种类。

完成状态至少分三个维度：

- `workflow_state`：READY/RUNNING/STOPPED/BLOCKED/COMPLETE。
- `evidence_level`：source/model/static/build/boot/probe/operation/stress/hardware，允许多个维度而非强行全排序。
- `obligation_status`：PLANNED/IMPLEMENTED/PASS/FAIL/BLOCKED/NOT_RUN/NOT_APPLICABLE。

核心规则：`required=true` 的行为义务只有在指定层级的证据满足时才闭合。阶段报告完成，不会自动关闭义务。`NOT_APPLICABLE` 必须有预先冻结的设备／目标依据；缺少能力通常是 BLOCKED，不能自动变为 N/A。

### 4.3 封存“契约和已知缺口”，不要封存错误前提

允许设计阶段完成时有 PLANNED 实现义务，但明确两种缺口：

1. 已确定实现方案的正常待实现工作：可以进入 implementation。
2. 必需能力仍未知、关键源语义不明、执行路线不存在：先做有界 capability spike。

例如“尚未编写 RX ring”不是设计未完成；“不知道目标 DMA 地址如何交给设备”可能阻断实现。对依赖这个未知事实的子任务阻塞，其余独立义务可继续。

保留现有 phase boundary 的审计价值，但增加显式版本化 reopen：finding→受影响义务→新设计版本→局部下游失效。新证据可以推翻旧前提；不是让模型静默改写冻结合同，也不是把可在项目内修复的平台缺陷统称为外部资源阻塞。

### 4.4 能力匹配采用需求交集

设备所需能力必须与目标提供能力和执行拓扑同时兼容：

```text
feasible(operation) =
  source/device requirements
  ∩ target capability semantics
  ∩ selected hardware/model/topology behavior
```

通用能力维度包括总线发现、MMIO/PIO、DMA 地址与一致性、中断类型和上下文、锁／睡眠约束、缓冲区所有权、计时、复位和生命周期。不是每个驱动都需要全部维度。

对每个关键能力，不只问“定义在哪”，还要问“操作能否发生、谁持有资源、错误如何返回、用什么可执行见证证明”。e1000 的 INTx 与 MSI-X 问题、NE2000 的 PIO accessor 返回 IoError，都能由这一机制表达，无需驱动专用门禁。

## 5. 让正确性检查更早、更便宜

### 5.1 在实现早期得到一条真实执行路径

建议不新增大量顶层 stage，而在现有 stage 内记录以下里程碑：

```mermaid
flowchart LR
    A[冻结范围与必需行为] --> B[关键能力小实验]
    B --> C[目标基线构建与启动路线]
    C --> D[驱动接入与真实 probe]
    D --> E[首个可观测设备操作]
    E --> F[完整范围与错误路径]
    F --> G[冻结候选与独立评测]
```

不同平台可以是完整 kernel build、合法模块装载或有依据的制品插入；不能默认只有全源码构建一种路线。基线启动证明环境可用，候选仍必须证明当前代码进入可执行制品并实际运行。

首个操作因设备而异：

| 驱动类型 | 最小行为见证 | 后续重要边界 |
|---|---|---|
| 网络 | 外部注入／捕获的 payload，与当前设备路径关联 | ring wrap、拥塞、IRQ、重置、并发 |
| 块／存储 | 写入再读回随机数据并核对，绑定实际设备 | 队列、完成乱序、超时、flush、失败恢复 |
| 串口／输入 | 外部刺激与接收字节／事件匹配 | FIFO、丢失、IRQ、流控 |
| RTC／定时器 | 设置／读取／事件触发的时间关系 | rollover、精度、唤醒、并发 |
| watchdog／panic | 寄存器操作与设备／模型事件关联 | 禁用、超时、重启和适用生命周期 |
| DMA／传输设备 | 数据完整性、所有权转移和完成事件 | 对齐、映射失败、ordering、取消 |

这张表用于说明适配器接口，并非穷尽所有驱动或所有必需测试。无 QEMU 模型的设备要使用明确分级的硬件测试或 mock 验证，不能把 mock 通过写成硬件驱动通过。

### 5.2 便宜的语义前置应有针对性

优先提取真正影响翻译的编译事实：活动宏分支、整数宽度／有符号性、descriptor 布局、位域、对齐、MMIO 访问宽度。配置、编译器、头文件和结果一起冻结。

将纯计算逻辑抽取为 host-side 差分测试，例如 descriptor 编解码、长度计算、ring 索引、状态转换。Rust property tests、fuzz、必要时 Miri 可以发现局部问题；不能把这些工具的通过推论为 MMIO/DMA/中断正确。硬件依赖路径需要模拟器／实机见证和内存模型分析。

C 驱动迁移跨平台不是逐函数转写。合同应描述可观察行为和资源约束，明确源内核特有接口如何适配，避免把 Linux API 表面相似当成语义等价。

### 5.3 由控制器或可信执行端解析 oracle

当前 `QemuHarnessResult.passed` 主要判断进程执行、退出、QEMU、runtime 绑定、非空新日志、容器边界。它是有效的机械证据门，但不理解“收发发生了没有”。

建议增加版本化 oracle 接口：输入为不可变的原始观察和 scope，输出为按 obligation ID 列出的结果、观测引用、适用层级。公开 oracle 在实现前冻结，模型提交解释不能替代判定器输出。

加强以下因果证据：

- nonce 或随机 payload 将刺激与结果关联；nonce 本身不是不可伪造证明，需要结合可信采集边界。
- 制品摘要→构建输入→组件链接→运行 instance→刺激→外部观察，形成完整链条。
- 负对照：设备缺席、错误设备 ID、禁用被测路径时，不得仍然产生同一功能成功结论。
- 观测命名区分 registration、probe-enter、probe-success、operation-complete；不能用同一 ID 字符串代表全部层级。
- 收集 raw serial／事件／PCAP，展示时再规范化 ANSI 等格式，不丢弃原始证据。

模型可以设计和维护公开测试，但候选最终质量必须依赖它不能随意改写的验收规范和独立测试。通过方式不是增加更多可由模型 `echo PASS` 的标记。

### 5.4 独立 review 按风险分配

不建议为了省钱无条件移除 review，也不建议每一步都增加第二个模型。

设计 review 在必需能力含 UNKNOWN、硬件／目标映射不一致、范围变更时价值较高。实现 review 优先关注 DMA 所有权、IRQ 锁顺序、unsafe 边界、错误清理及目标框架变更。即使没有 unsafe，也可能发生完全错误的硬件行为。

机械检查负责可确定判定的事项；模型 review 负责跨证据语义和遗漏；独立盲测负责功能泛化。三者不是替代关系。正式实验的必要质量门不能由候选自己关闭。

## 6. 修复控制：从字节变化转向义务进展

### 6.1 分类再行动

| 失败类别 | 首选处理 | 何时调用模型 |
|---|---|---|
| 网络、下载、provider 断连 | 同请求幂等恢复、有限退避、checkpoint | 多次失败需要更换已授权路线时 |
| 提交格式／缺字段 | schema 诊断、局部纠正 | 静态无法修复语义时 |
| 工具／编译／环境 | 平台 recipe、最短复现 | 确实需要诊断新问题时 |
| 制品接入／构建 | 重建受影响制品、验证链接和入口 | 接入方案不明时 |
| 采集器／harness | 校准采集路径、只修 observation 链 | 归因不清时 |
| 驱动行为 | 绑定失败刺激与义务，定位最小代码范围 | 需要语义修复时 |
| 契约／能力前提 | 有界调查，显式 reopen | 前提被证伪时 |

分类记录置信度和证据。无法区分 driver 与 oracle 缺陷时应标 INCONCLUSIVE，执行区分性实验；不能直接把一方当作事实。

### 6.2 进度记录

保留现有字节指纹作幂等保护；另存义务进展向量：已关闭必需义务数、可达执行里程碑、稳定错误集合、当前诊断假设和区分性实验结果。

建议起始策略（属于待调参方案）：两次同类失败且无新证据，停止原续调方式；生成一份短诊断包，选择局部调查、一次新会话诊断、上游 reopen 或明确阻塞。不能无限“换个模型再试”。

指纹、已尝试假设、已消耗预算和诊断次数应持久化到 run/attempt，而不是只在 `_run_codex_task` 的局部变量里。重启不恢复免费重试额度。

不要只用“通过数量增加”判断进展：证伪错误假设也是有价值的进展。但必须有新观测，不接受只重写报告或改变 sleep 时间作为无限续调理由。数值阈值要通过开发集调节，不能宣称两次适用于所有复杂驱动。

## 7. 对“让模型忘记上下文”的具体评价

### 7.1 区分四种机制

| 机制 | 是否真正去掉旧信息 | 成本／质量作用 |
|---|---|---|
| Prompt cache | 否 | 降低重复前缀处理成本；旧信息仍参与上下文 |
| 原生 compaction | 部分压缩保留 | 缩短历史，但可能保留错误假设或丢失细节 |
| 新会话＋结构化交接 | 可以选择性丢弃 | 控制携带哪些事实，需支付交接、冷缓存和重新读取成本 |
| 声称“忽略之前内容” | 不保证 | 若历史仍在请求中，不能据此声称降低输入费用 |

OpenAI 官方 [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) 说明缓存依赖匹配前缀，会话持续并不保证命中；[Compaction](https://developers.openai.com/api/docs/guides/compaction) 说明压缩保留后续所需状态。这里将其作为机制依据，不把 Responses API 的接口或加密 compaction 项直接等同于本地 Codex CLI 的实现或中转服务行为。

### 7.2 当前项目已有的上下文优化

`codex/sessions.py` 在 developer 模式下用持久 worker 跨阶段续接，reviewer 单独保存；`input_changes` 标注产物变化，`prompts.py` 对已知 Skill 文档只传 unchanged 引用。首次 implementation 在条件满足时使用 160k compact 阈值，其后通常为 224k。

这些是已有优化，不应建议“从零增加会话复用”。但 `documents` 已发送不代表压缩后仍被完整记住；更不能在新 thread 中沿用 old thread 的 known_documents，导致只发送 hash 而没有规则正文。

本次累计输入接近 1,300 万 token，不能据此认定上下文超过窗口，也不能用该数字触发重置。应观测当前上下文、最近一轮 usage、压缩事件和局部工作进展。

### 7.3 推荐：外部完整记忆＋短期工作上下文

采用三种存储：

1. **不可丢弃的外部记录**：冻结 scope、必需义务、权限／范围、代码与制品摘要、失败 receipts、原始证据及历史决策。
2. **可验证的当前状态包**：当前里程碑、已验证事实、待验证假设、未决义务、失效旧结论、下一项区分性实验，附精确证据指针。
3. **可丢弃的交互历史**：重复 grep、过期构建日志、已经否定的推理过程、无关 acquisition 对话、报告修辞和格式纠错。

不是从系统中删除历史，而是让模型不再每轮处理全部历史。模型摘要可能犯错，所以状态包中的状态和 digest 应优先由控制器生成；模型只写紧凑的解释，必须标记 `verified / hypothesis / refuted / unknown`。

特别不能丢失：DMA 生命周期、锁顺序、IRQ 上下文、尚未执行的测试、禁止改变的范围、曾经触发的失败输入。否则“遗忘”会变成重复踩坑或隐性降级。

### 7.4 何时重置

| 情况 | 建议 |
|---|---|
| 获取资料→编写实现，资料历史庞大且契约已完整 | 考虑新会话，携带当前义务、目标 API 与构建路线 |
| 同一局部编译错误，上一轮刚形成有效定位 | 继续当前会话，保留局部推理 |
| 多轮在同一失败上改表象，存在过期归因 | 一次新诊断会话，只给代码、事实、失败和必要约束 |
| 目标 revision、scope 或主要架构前提变更 | 新 epoch；旧结论显式失效，重新加载规则 |
| 独立最终评审／盲测 | 使用隔离上下文和访问权限；不继承作者说服性叙事 |
| 任意累计 token 达到固定值 | 不足以决定重置；先检查真实上下文和收益 |

对主案例，适合在发现“真实数据路径不存在”后结束采集形式续调，形成新诊断包：当前代码是外壳、required C04–C07 未完成、marker 不是制品、已有容器可观测、INTx 映射待核实。新会话不能被交接成“驱动已完成，只差 collector”。

### 7.5 成本模型与实验阈值

在沿用本项目历史 short-context 费率的归一化示例中：

```text
C = ((I - H) × p_input + H × p_cached + O × p_output) / 10^6
```

其中 I 是累计输入，H 是其缓存部分，O 是输出。若 provider 有单独 cache-write、长上下文或不同 tier，需按实际不重叠计费字段重算。当前 sidecar 估算不是官方现价验证，也不是中转账单。

例如以代码中 4 / 0.4 / 20 的历史归一化价格：丢弃 100k 个旧 token，假设每次原本均缓存命中，则每次请求减少 $0.04；重建 12k uncached 输入和 2k 输出约 $0.088。忽略其他代价时约三次后续请求才覆盖重建成本。这个例子只说明有盈亏平衡点，不是预计节省率；实际还需加入重新读取、cache write、压缩、额外错误和返工成本。

决策应比较：

```text
保留历史的预计后续费用与错误成本
  > 新状态包 + 冷启动 + 重读 + 遗忘引发错误的预计成本
```

初期可测试 8k/16k/32k 的状态包预算，不把它们当产品默认定律。先限制工具输出，往往比频繁换会话更直接：按 symbol/section 检索，编译输出先返回错误摘要与日志路径，保留原文供定位。不能为了短而截断关键调用链或隐藏失败。

### 7.6 最小实现位置

- `codex/sessions.py`：增加 `conversation_epoch`、`context_policy`、reset 原因和 handoff digest；保留同一调试任务的 resume。
- `codex/prompts.py`：新 epoch 重新加载必要规则，稳定规范置于可复用前缀，动态任务／失败置后；正文引用不冒充已经阅读。
- `codex/cli.py`、`gateway.py`：记录请求和 usage 边界，重置不破坏计量链；核实实际 CLI 事件语义。
- `port.py`：根据持久化停滞事实调用 reset 决策，不把每个 stage 都强制变成新线程。
- CAS／ledger：保存 context packet 与输入 identity，支持准确回放“当时模型看到了什么”。

## 8. 成本治理：优化到达正确结果的总费用

### 8.1 先补计量，再做模型路由

每次调用记录 provider/model/version/tier、开始／终止状态、request ID（可得时）、usage 是否请求级或累计级、cached／uncached／cache-write／output、压缩事件、估算费率版本、实际账单对账状态。

中断调用通过 finally/checkpoint 写入 terminal/interrupted，避免缺少 completed_at 被无限计时。累计计数缺失时保持 UNKNOWN；不要跨缺失区间把后一个 delta 全算到最后阶段，也不要随意补零。

预算应有 run、phase、义务修复三个层级。未知 usage 的调用也消耗调用数和时间预算。区分硬上限与需要诊断的软阈值；预算耗尽输出明确的未完成状态和可恢复快照，不许降低质量门限凑成功。

### 8.2 模型分工以实际难度为依据

确定性状态检查、hash、依赖、schema 和已知环境恢复交给程序。简单证据定位／归类可以评估较便宜模型；DMA、并发、生命周期与跨平台语义保留能力强的模型。不要仅按文件长度或 stage 名称分配。

升级模型应由新信息不足、诊断不确定或高风险义务触发，并有一次有界诊断任务。切换模型也可能失去缓存，且同模型自评容易共同遗漏。更强模型和更多 review 是否划算，只能由 matched experiments 判断。

### 8.3 能复用的东西优先复用

- 源检索和平台知识按 revision／内容摘要复用。
- 构建缓存按工具链、配置、依赖和源内容复用，保留构建证明。
- runtime receipt 绑定制品、驱动、框架、harness、oracle、validator、固件／QEMU／容器身份；只复用适用证据。
- 报告文字变化不重跑执行；oracle 或代码变化重新跑受影响测试，语义解释的重评可使用冻结原始日志但必须记录新 evaluator 版本。
- 平台 recipe 的一次性建设成本和每个驱动的边际成本分别报告，并给出摊销假设。

### 8.4 正确的成本目标

主指标建议为固定质量下的 success@budget，以及：

```text
每个合格迁移的摊销成本 = 全部尝试成本 / 合格迁移数量
```

分子包括失败和重试。另报中位数／p90 成功费用、time-to-first-real-operation、总 wall time、人工分钟、环境建设成本。若无成功样本，该比值不可计算，不能报“低成本”。

缓存命中率、token 数、PASS stage 数是诊断指标，不能替代迁移质量。

## 9. 面向工程顶会的研究组织

### 9.1 可以成立的贡献主张

建议围绕一个统一命题组织，而非把阶段数和工具数量当贡献：

> 将跨内核驱动迁移表示为带证据和运行见证的行为义务图，使用义务状态驱动验证、局部修复和上下文保留，在可复现质量门限下提高迁移成功率并降低总成本。

可能的三个贡献：

1. 区分平台适配、设备语义和执行观察的通用义务表示与可复现运行体系。
2. 利用证据进展进行最小修复和上下文选择，控制重复交互成本。
3. 覆盖不同设备类别／平台能力的 benchmark、失败分类、公开制品与独立评测。

本文是工程假设和本地审计，不是系统文献综述，也未证明相对已有论文的新颖性。投稿前必须核对自动驱动生成、C→Rust、安全内核抽象、agent memory 与 program repair 的相关工作。

### 9.2 研究问题

| RQ | 问题 | 主要指标 |
|---|---|---|
| RQ1 | 通用流程能迁移哪些设备行为，失败在哪里？ | 按设备／能力分层的合格率、义务覆盖、失败阶段 |
| RQ2 | 义务门禁与早期真实操作是否减少假成功和晚期返工？ | false acceptance、首次设备操作时间、返工费用 |
| RQ3 | 选择性遗忘何时优于长会话／原生压缩？ | 固定预算成功率、总费用、遗忘错误、重复调查 |
| RQ4 | 平台知识、风险 review、最小修复分别有什么贡献？ | 配对消融、缺陷发现率、误拒绝率、成本差 |
| RQ5 | 已通过公开测试的候选能否泛化？ | held-out 行为、fault injection、并发与硬件子集 |

### 9.3 Benchmark 必须体现通用性

按能力而非驱动名字采样：简单 MMIO/event、PIO、DMA ring、块队列、IRQ／异步回调、错误恢复、共享核心代码、多设备实例。覆盖网络、存储、串口／输入、计时／系统设备等不同类别。

建议先用 3–5 个开发驱动调通系统，再按可获得设备、目标 API 和算力确定 12–20 个正式候选的可行规模；数量只是规划目标，不是已具备的数据集。若只有 Linux→Asterinas，应准确称为“该平台对上的跨设备通用性”，不能宣称跨任意内核泛化。跨平台主张至少需要额外平台对或明确的适配器迁移实验。

测试集在尝试前登记：源／目标／QEMU revision、硬件族、配置、功能范围、支持／不支持能力、必需行为与 N/A 依据。包含能力缺失与失败任务，不只筛选容易成功的驱动。e1000/e1000e 或共享 8390 core 的近亲任务不能被随意当作完全独立 held-out 样本。

### 9.4 基线与消融

先控制模型与环境，再比较流程：

| 实验臂 | 内容 |
|---|---|
| B0 | 直接 agent＋同等公开资料＋相同预算，无本流程编排 |
| B1 | 当前冻结 DPF，原样报告其限制 |
| B2 | 义务完成语义＋早期运行能力验证＋最小因果修复 |
| B3 | B2＋选择性遗忘／有验证的状态包 |

对上下文单独比较：持久会话、持久会话＋固定 compaction、每 stage 新会话、阶段边界新会话、停滞触发新会话。必须控制或计量提供的知识、状态包质量和检索预算，否则“少读资料”与“遗忘策略”混为一谈。

分两步实验更节省成本：先在固定失败 checkpoint 上分叉 continuation 做配对诊断实验，再在全新 end-to-end 任务上验证总体收益。checkpoint 结果只能证明局部恢复效果，不能代替全流程成功率。

不建议一开始做所有因素的笛卡尔积。开发集筛选策略，正式集冻结主对比和少数关键消融。移除 review 的实验仍由统一外部评测判质量，不能让各实验臂自己定义成功。

### 9.5 独立测试与数据隔离

迁移 worker 只能访问公开契约和公开测试。候选冻结后，独立测试方设计／执行私有断言；对后验候选要明确标注 post-hoc sealing，不能写成预注册前瞻实验。新的测试迭代不能把私有失败细节反馈给同一候选然后继续称盲测。

独立不能只靠不同聊天线程：目录、工具权限、日志、缓存和知识库必须隔离。当前 `session_key` 里的 reviewer 分离提供会话边界，不自动提供文件访问隔离或独立性保证。

质量测试包括：公开设备测试意图迁移、未见输入、超时／分配失败／异常完成等 fault injection、资源与状态不变量、可执行 C/Rust 差分。跨 OS 差分比较设备相关语义，规范化调度时间和平台记账差异；C 的未定义行为不能当成 Rust 必须复现的规范。

QEMU 用于可复现测试，但不充分代表真实 DMA coherence、总线时序和物理设备 quirks。根据资源选取实机子集，分别报告模拟器与硬件结论；若无法实机验证，收缩论文主张。

### 9.6 统计与复现

每个正式任务进行多次独立运行，先用 pilot 估计方差再确定重复数；建议初始 3–5 次用于估算，而非声称已经有足够统计功效。配对冻结 revision、prompt、模型、预算、工具链、缓存策略和范围，随机化策略运行顺序。

报告所有失败、中断、人工干预、未知费用和超时删失，不能只对成功样本比较价格。用按驱动／设备族聚类的 bootstrap 或合适配对模型估计不确定性，避免把每个包或每条断言当独立样本。绘制 success–cost 曲线和 Pareto 前沿，另报 conditional cost 和全体成本。

Artifact package 应包含冻结代码与策略、生成的公开候选、配置与构建 recipe、模型/provider 版本、prompt/context 包、计量规则、测试 oracle 版本、摘要和复现命令。私有断言按评测协议封存。容器不能只存可漂移 tag，还应记录 digest、QEMU、固件、CPU／加速方式和 seed。

## 10. 实施优先级与验收

### P0：先使状态可信，避免继续烧在假前提上

| 工作 | 主要落点 | 验收 |
|---|---|---|
| 分离阶段完成与行为完成 | `core/models.py`、`control/statistics.py`、migration validators | model-only、probe-only、required NOT_RUN 不显示完整迁移成功 |
| 少量结构化义务＋阻塞依赖 | `migration/contracts.py`、target study、handoff | 必需能力未知时触发有界调查；正常 PLANNED 实现可继续 |
| 持久化重复失败与终止状态 | `port.py`、ledger、codex transport/CLI | 重启不清空停滞／预算；缺 completed_at 不膨胀计时 |
| 公开 oracle 接口和机械／功能判定分离 | `migration/public_qemu.py`、执行适配器 | QEMU 运行＋日志文件不能独自闭合设备操作义务 |
| 隔离测试复现条件 | tests fixtures／测试说明 | localhost fixture 不受代理影响，unit 测试无需真实外网 |

先用历史失败制品做离线反例，不必反复付费迁移：marker、暂停 guest、源 archive 挂盘、错误架构、只有 registration、伪造成功日志、过期制品均应得到准确分层结果。同时测试合法暂停后继续、非全源码构建、合法脚本型打包等正例，避免只针对失败文本打补丁。

### P1：提高真实进展速度

把 pvpanic 中可复用的平台路线整理为带适用条件的可执行 recipe；将能力 spike、构建／接入验证放在大量翻译前。对依赖图中的 DMA、IRQ、生命周期等高风险义务做定点 review；工具输出按需读取。测试“平台可运行，但候选未接入”与“候选接入，但首个操作失败”的正确归因。

### P2：实现上下文策略并验证收益

增加 epoch、状态包、reset 原因和 token／成本追踪。选择开发集真实停滞点，配对比较 resume、compaction、新会话。验收首先要求必需约束和失败事实不丢失，然后测节省；不以状态包更短作为成功标准。

### P3：冻结科研系统

锁定工厂 commit、Skill、适配器、模型和实验计划，再跑正式 benchmark。把新增设备／平台需要多少人工适配工作也作为结果。任何对主案例定向调整都留在开发集，held-out 任务用于检验泛化。

## 11. 对这次 e1000 后续运行的具体建议

不建议直接用原会话继续修 collector，也不建议先换最强模型完整重跑全部阶段。

1. 保留旧 workspace、失败 receipts 和统计，另建可追踪的后继 attempt／run。
2. 重核必要能力，尤其是所选设备实际中断模式、DMA 和网络 trait 所有权；区分已有 API 与可执行映射。
3. 借鉴同 revision 的 pvpanic 平台构建／UEFI 路线，验证当前目标基线和新组件真正接入；这不代表复用 pvpanic 的功能结论。
4. 在正常义务图中把 e1000 外壳标为 PARTIAL；逐步实现真实 reset、ring、收发与中断。不能让 Loopback 满足硬件网络合同。
5. 首个真实操作成功后再扩展边界、恢复和回归；覆盖不足不能靠缩小 public oracle 自动获得完整成功。
6. 在这次前提纠正边界尝试新 epoch，以核实事实和未决义务交接；保留旧历史供审计，避免继承“只差观察工具”的错误任务描述。

这些动作是通用机制在 e1000 上的应用，不应写成 `if driver == e1000`。本文未执行上述迁移，以免改变待研究样本和引入新的付费数据。

## 12. 本次验证、证据入口与限制

核对了主运行 SQLite、28 份 metrics、Codex 命令事件、12 个 smoke receipts、当前工作树、设计／框架／实施报告，以及近邻项目公开账本和证据。审计快照保存所引用 CAS 文件哈希核对结果；原始运行在 `/tmp` 中，正式研究应移入持久化归档，摘要 JSON 不能替代完整复现材料。

只读调用当前 `inspect_implementation` 对旧 e1000 工作树返回 `non-bootable-runtime-marker` 和 `qemu-paused`，没有运行 QEMU。

测试环境的 `.venv` 没有 pytest，使用 uv 临时依赖环境，未修改项目依赖。首次 21 项中有 2 项在本地 HTTP fixture 被代理返回 502 时失败；只调整测试命令的 localhost 代理排除后，21 项通过（11.96 秒）。通过的是控制器测试，不是驱动功能验证。

```sh
NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost PYTHONPATH=src \
  uv run --no-project --with pytest --with jsonschema --with referencing \
  python -m pytest tests/test_implementation_preflight.py \
  tests/test_implementation_smoke.py tests/test_optional_reviews.py \
  -o addopts='' -q
```

代码导航：

- [会话身份与 compact 阈值](../src/driver_port_factory/codex/sessions.py)
- [Prompt 文档去重](../src/driver_port_factory/codex/prompts.py)
- [续调与停滞指纹](../src/driver_port_factory/port.py)
- [smoke 与复用](../src/driver_port_factory/migration/implementation_smoke.py)
- [通用 preflight](../src/driver_port_factory/migration/implementation_preflight.py)
- [QEMU 机械观察](../src/driver_port_factory/migration/public_qemu.py)
- [费用估算](../src/driver_port_factory/codex/accounting.py) 与 [时间统计](../src/driver_port_factory/control/statistics.py)
- [phase 边界](../src/driver_port_factory/core/phases.py)
- [历史成本审计](RUN_COST_AND_STABILITY_AUDIT.md) 与 [已有前置检查修复](REAL_RUNTIME_PREREQUISITE_AUDIT.md)

不能由本次审计得出的结论：新会话一定更便宜、更聪明；所有独立 review 都值得花钱；某个更强模型必能完成；pvpanic 的成功证明所有驱动可迁移；QEMU 成功证明实机安全；当前 $9.3260 是完整成本。上述问题需要本文提出的受控实验回答。
