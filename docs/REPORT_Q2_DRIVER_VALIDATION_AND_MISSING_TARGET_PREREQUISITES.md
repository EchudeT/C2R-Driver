# 问题二：驱动验证、Asterinas 前置能力缺失与阶段门禁

## 1. 报告范围和结论

本报告回答三个问题：

1. 如何验证一个从 C 迁移到 Rust 的内核驱动；
2. 如果 Asterinas 中缺少驱动所需的前置能力，如何继续迁移并判断应该补哪一层；
3. 如何让每个迁移阶段都有与职责相匹配的验证，而不是用“编译成功”或“模型说完成”代替真实证据。

依据包括本项目使用的原版 `open-kernel-driver-port` 与
`knowledge-guided-driver-port`，以及当前 `driver-port-factory`（DPF）的
[`STAGE_GUIDE.md`](STAGE_GUIDE.md)、[`WORKFLOW.md`](WORKFLOW.md)、
[`TEST_ADAPTERS.md`](TEST_ADAPTERS.md)、[`EXECUTION_RECOVERY.md`](EXECUTION_RECOVERY.md)
和 [`SKILL_TRACEABILITY.md`](SKILL_TRACEABILITY.md)。

结论先说清楚：

- 驱动验证必须验证“合同”，而不是只验证 Rust 能否编译。一个有效结论至少需要明确前置条件、刺激、独立观察、断言、清理、运行身份和结果归因。
- 测试结果必须分开说明硬件协议、源内核语义、Asterinas 集成、QEMU 模型和外部 I/O。source baseline、独立 QEMU model smoke、驱动日志或一次成功启动，都不能单独证明迁移驱动正确。
- Asterinas 前置能力缺失时，先判断缺失的是证据、目标 API、集成 wiring、artifact 包装、环境还是 QEMU 模型；然后按“驱动自有改动 → 最小集成 wiring → 有证据的目标 API/framework 改动”逐级处理。不能因为 Linux 有某个 API，就直接在 Asterinas 中按名称发明同名接口。
- 每个 DPF 阶段都应有自己的 required output、确定性 validator、证据状态和执行状态。阶段 `PASS` 只能表示本阶段冻结的门禁通过，不能向后续阶段传播未验证的更强结论。
- 当前 DPF 已经有完整的控制器/阶段骨架、状态隔离、QEMU receipt 和独立审查设计，但追踪矩阵仍把完整证据闭包、迁移合同、测试 provenance、artifact insertion proof、完整 QEMU ladder、窄修复和最终合同审计标为 `PARTIAL` 或 `PLANNED`。报告中必须把“规范要求”和“当前实现”分开。

## 2. 驱动验证的基本模型

### 2.1 验证对象不是一个二值的“驱动通过”

一个驱动迁移会产生多种不同强度的声明。每个声明都应该写成如下记录：

```text
claim_id
contract_ids
preconditions
stimulus
observation/oracle
cleanup
artifact and revision identity
run_id
evidence_status
execution_status
attribution
```

例如，下面三句话的证据强度完全不同：

| 声明 | 能证明什么 | 不能证明什么 |
|---|---|---|
| Rust crate 编译通过 | 语法、类型和选定编译配置可接受 | 驱动已进入镜像、设备已匹配、TX/RX 正确 |
| QEMU `ne2k_pci` standalone qtest 通过 | QEMU 模型满足某个预先声明的模型合同 | Asterinas 中的 Rust 驱动已走到该路径，更不能证明真实硬件行为 |
| 外部观察到当前 artifact 发出正确帧并接收正确 payload | 当前产物在该 QEMU 路径上的某个 I/O 合同通过 | 未执行的边界、恢复、真机兼容性或其他设备变体 |

因此，最终报告不能只出现一个总分。至少要分别报告：

- **硬件/设备协议**：寄存器宽度、页/Bank 切换、复位、ring/descriptor、长度、DMA、IRQ 和时序；
- **源平台行为**：C 代码、回调、锁、资源生命周期和错误展开；
- **目标平台集成**：Asterinas 注册、匹配、资源获取、生命周期、同步、错误和 artifact 包装；
- **QEMU 模型**：模型支持的设备、选项、时序简化、注入点和不可观察行为；
- **外部功能**：包、帧、块、串口字节流、计数器或其他目标外部可观察结果。

原版 Skill 的知识库合同也要求将硬件、source platform、target platform、QEMU 四条证据链分开；一条实现代码不能自动充当硬件规格，一段目标示例代码也不能自动充当规范 API。

### 2.2 两类状态必须分开

原版 QEMU 规则和 DPF 的运行记录均要求分离“证据状态”和“执行状态”：

| 证据状态 | 含义 |
|---|---|
| `VERIFIED` | 有直接打开的源码/文档/运行观察支持 |
| `INFERRED` | 可由现有证据推理，但还未直接观察 |
| `PLANNED` | 已冻结测试计划或 oracle，尚未执行 |
| `NOT_RUN` | 没有符合条件的运行 |
| `NOT_APPLICABLE` | 目标合同中没有这个操作 |
| `BLOCKED` | 有名的前置能力或证据缺失 |
| `FAIL` | 符合条件的执行违反了冻结 oracle |
| `PASS` | 符合条件的执行满足全部冻结 oracle |

执行状态也使用 `NOT_RUN/NOT_APPLICABLE/BLOCKED/FAIL/PASS`，但它只描述该次操作，不替代证据来源。例如，QEMU 可以对一个 standalone model oracle 得到 `PASS`，同时迁移驱动运行仍为 `BLOCKED_FULL_INTEGRATION`；一个测试也可以因为 Asterinas 缺少动态卸载机制而为 `NOT_APPLICABLE`，不能被伪造成 unload 通过。

## 3. 问题一：如何做到驱动验证测试

### 3.1 从源测试开始，但不把源测试照搬成目标测试

原版 [`test-porting.md`](/home/unix/file/C2R-Driver/C-kernel-to-Rust/skill/knowledge-guided-driver-port/references/test-porting.md) 要求对每一个发现的源测试指定一个主分类，并保存分类证据。分类如下：

| 分类 | 处理 | 判断标准 |
|---|---|---|
| `DEVICE_FUNCTIONAL` | 保留 | stimulus 和 oracle 直接针对外部可见的设备/驱动行为 |
| `DEVICE_PROTOCOL_INTERNAL` | 保留或适配 | 寄存器序列、ring/descriptor、状态转换、边界、超时或恢复规则 |
| `PORTABLE_INTENT_PLATFORM_HARNESS` | 适配 | 驱动意图有效，但夹具或观察接口使用了 Linux API |
| `SOURCE_PLATFORM_SEMANTICS` | 排除并记录 | 只测 Linux 内部 ABI、模块装载、scheduler、sysfs/procfs、framework bookkeeping 等 |
| `OUT_OF_SCOPE_DEVICE_VARIANT` | 排除并记录 | 设备、总线、架构、模式或可选特性超出冻结范围 |
| `TARGET_CAPABILITY_BLOCKED` | 保留意图但不计入通过 | 目标缺少非驱动能力，测试设计本身仍有效 |
| `QEMU_MODEL_BLOCKED` | 保留意图但不计入通过 | QEMU 缺少或不能观察所需硬件行为 |

不能只按测试文件名、注册表名称或函数名分类。必须读取实际的 setup、stimulus、oracle、断言和 cleanup，并逐个回答：

1. 断言的驱动/硬件要求是什么；
2. 换一个内核后该要求是否仍存在；
3. stimulus 是否真的送到了设备，而不是只改动了 Linux 内部状态；
4. Asterinas 是否有等价的观察方式，例如串口、计数器、packet capture、QMP/qtest 或外部流量；
5. QEMU 是否实现并可控制该行为；
6. 适配是否保持了时序、顺序、边界、负路径、清理和失败语义。

一个测试包含混合断言时应该拆分：保留设备断言，移除没有迁移价值的 Linux bookkeeping 断言，并在 mapping record 中列出被移除的部分和原因。

### 3.2 测试合同矩阵

每个保留、适配或新增的公开迁移测试都需要一条稳定的 mapping record，至少包括：

```text
source_test and provenance
original command/framework
class and rationale
driver_contract_ids
setup
stimulus
oracle/assertions
cleanup
source-only assertions removed
target adaptation and target evidence
QEMU prerequisites/limitations
expected result
actual result and run_id
status
```

推荐以“合同 → 测试 → 观察”做矩阵，而不是只列测试文件：

| 合同类别 | 代表性测试 | 独立观察 | 常见误判 |
|---|---|---|---|
| 设备匹配 | 精确 PCI vendor/device/class 匹配、错误设备拒绝 | 设备 ID、绑定记录、负匹配计数 | 只看到注册函数执行就算 probe 成功 |
| 资源与初始化 | BAR/PIO/MMIO 获取、reset、MAC/identifier 读取 | 资源范围、寄存器快照、ready marker | 只检查 API 返回值，不检查硬件状态 |
| TX | 单帧、重复帧、合法长度、padding、完成/超时 | backend 收到的方向、长度、payload、协议字段 | 只看驱动返回 0 或日志 |
| RX | 合法帧、短帧、超长帧、坏字段、重复收包 | 上层收到的 payload、丢弃计数、校验结果 | “收到一个中断”被当成数据正确 |
| ring/descriptor | wraparound、ownership、长度边界 | descriptor 状态、数据完整性、重复操作结果 | 只覆盖第一次收发 |
| IRQ/deferred work | mask、ack、re-enable、真实中断后的处理 | QEMU IRQ/QMP、串口 marker、继续收发 | polling 路径通过被当成 IRQ 通过 |
| 错误与恢复 | timeout、malformed input、reset/retry、资源失败 | 预期错误、无越界/泄漏、恢复后有效 I/O | fault hook 本身通过被当成生产代码通过 |
| 生命周期 | registration、probe/start、stop/detach、reset、reboot/reinitialize | 资源释放、重复初始化、冷启动结果 | 目标无动态模块卸载却伪造 Linux unload |

具体矩阵要由硬件合同和实际源测试推导；这些是候选覆盖项，不是脱离设备的固定测试清单。

### 3.3 测试适配层应以设备行为为中心

本项目的 [`TEST_ADAPTERS.md`](TEST_ADAPTERS.md) 给出的结构是：

```text
Scenario + Oracle
       |
DeviceClassProtocol
  /             \\
Linux fixture   Target fixture
  \\             /
 QEMU or hardware backend
```

这样分层有三个作用：

- **设备插件**定义 scenario、stimulus、observation 和 oracle，例如网络帧、块读写序列、串口字节流、寄存器时序或恢复条件；
- **平台插件**只负责设备发现、生命周期和 Asterinas/Linux 的外部接口；
- **QEMU/hardware backend**提供真实的注入和观察路径，而不是让测试直接断言内部 Rust 对象。

这种设计允许 Network、Block、Serial 等设备类别复用设备合同，同时避免强迫所有平台实现一个失真的万能驱动接口。源测试中的 Linux fixture 可以被替换，但设备意图、数据、边界、负路径和 oracle 必须保持可比。

### 3.4 生命周期测试必须服从 Asterinas 的真实机制

源 Linux 测试经常含有 `module load/unload` 或动态解绑。Asterinas 如果没有证据表明支持动态模块卸载，就不能把该操作硬翻译成一个不存在的 API。正确做法是：

- 目标确实支持动态注册/卸载时，验证注册、probe、stop、detach 和释放；
- 目标只支持 component registration 或镜像内静态选择时，把生命周期意图改写为 registration、probe/start、reset、reboot 或 repeated cold boot；
- 不存在的源操作标为 `NOT_APPLICABLE`，同时说明目标真实生命周期和保留的验证意图。

`NOT_APPLICABLE` 不等于“整个驱动验证通过”，也不等于“测试失败”；它只说明该源操作不能作为目标声明。

### 3.5 QEMU 验证必须按 evidence ladder 推进

原版 [`qemu-evidence.md`](/home/unix/file/C2R-Driver/C-kernel-to-Rust/skill/knowledge-guided-driver-port/references/qemu-evidence.md) 要求每次实质 QEMU 运行前冻结计划、运行目录、命令、镜像和 oracle，并按以下梯度推进：

| 级别 | 必须证明 | 通过后才能声称 |
|---:|---|---|
| 1 | environment smoke：固定 QEMU/device route 真实启动并退出或有界超时 | 环境路线可执行 |
| 2 | static/style：目标规定的静态检查 | 当前修改满足适用静态规则 |
| 3 | artifact preparation：编译/注入/overlay/repack 成功，记录 base/final identity | 有一个可定位的候选产物 |
| 4 | driver presence：包装中有当前驱动 hash 或绑定 marker | 当前驱动确实进入待运行 artifact |
| 5 | enumeration/probe：精确设备、资源、binding、初始化和 ready marker | 当前驱动已匹配并完成 probe |
| 6 | single data operation：至少一个外部可观察 TX 和 RX（适用时） | 一条基础数据路径通过 |
| 7 | interrupt/deferred work：真实 IRQ、ack、继续工作；与 polling 分开 | 中断路径通过 |
| 8 | retained migrated tests：执行选定设备测试 | 公开迁移测试通过 |
| 9 | boundaries/recovery：合法边界、畸形输入、timeout、重复 I/O、reset/retry 和恢复后有效流量 | 负路径和恢复合同通过 |
| 10 | regression：干净/冷启动和代表性完整流程重复 | 约定的回归条件通过 |

每一级都有自己的 oracle。前一级没有满足时，后一级必须是 `NOT_RUN`、`BLOCKED` 或其他明确状态，不能写成 PASS。例如没有 driver-presence proof 时，即使 QEMU 退出码为 0，也不能把 probe 记为通过；没有外部帧观察时，驱动内部返回成功不能替代 TX/RX oracle。

### 3.6 运行前后的身份和控制检查

每一次有实质意义的 QEMU run 都要保存：

- source/target/QEMU revision、dirty-state fingerprint；
- base image/container、Rust module、injected component/overlay、最终 artifact、迁移测试 binary 的 hash 或构建身份；
- QEMU 命令、设备拓扑、CPU/memory、backend、超时和清理方式；
- stimulus、预期 marker、成功/失败 marker 的互斥关系、计数器和阈值；
- stdout/stderr/serial、packet capture、QMP/qtest、helper/checker 输出、退出码和时间；
- expected-versus-actual 表、status、attribution、contract/test IDs 和唯一 `run_id`。

运行前要检查端口、socket、lock、helper 服务和由本次 run 创建的 QEMU 进程；运行后只终止本次 run 创建的进程，不能用记忆中的 PID 判断实验是否仍在运行，也不能覆盖旧失败。

harness 与 oracle 要独立审计：成功和失败 marker 不能同时被接受，payload generator 与 checker 必须匹配，所有输出必须写入当前 run 目录，阈值要在运行前冻结。修改代码或 harness 后必须产生新 run ID，先重跑最小受影响测试，再跑依赖回归。

### 3.7 失败归因必须精确

| 观察 | 正确结论 | 不能推出 |
|---|---|---|
| source baseline 通过 | 源实现/源环境满足该源 oracle | Rust/Asterinas 驱动通过 |
| standalone QEMU model 通过 | QEMU 模型前置条件满足 | 迁移驱动已进入 artifact |
| 驱动日志出现 | 某段代码路径执行过 | 外部 I/O、payload、长度或恢复正确 |
| 没有触发预期事件 | `NOT_OBSERVED` 或 `QEMU_MODEL_BLOCKED`，视证据而定 | 自动判为 driver FAIL 或 PASS |
| harness 控制失败 | 该结论无效，需要修复控制路径并重跑 | 驱动失败 |
| QEMU 未发现设备 | 可能是设备拓扑、artifact、包装、模型或 probe，需沿路径归因 | 直接归咎 Rust 驱动 |
| 目标缺少动态卸载 | 该操作 `NOT_APPLICABLE` | 目标整体不支持驱动 |

一个临时 fault hook 只能证明 instrumented configuration；恢复生产代码后必须重新执行。一次成功启动也不是稳定性证据，稳定性需要预先约定的多次 clean/cold start 条件。

### 3.8 Asterinas/NE2000 场景示例

对于 Linux `ne2k-pci` 到 Asterinas、QEMU `ne2k_pci` 这类迁移，至少应把以下声明分开：

1. 当前 Rust 驱动的 PCI 设备 ID 与范围匹配正确；
2. BAR/PIO 或 MMIO 资源、复位、8390 shared core 的寄存器页/Bank 访问符合硬件合同；
3. 当前 artifact 确实含有此次迁移的驱动和目标集成改动；
4. QEMU 中该驱动完成 probe，而不是只有设备模型启动；
5. 外部 backend 看到正确方向、长度和 payload 的 TX/RX；
6. IRQ acknowledgement、mask/re-enable、ring/descriptor、timeout、reset/retry 和重复冷启动符合测试计划。

本项目的 [`E2E_NE2000_RUN_AUDIT.md`](E2E_NE2000_RUN_AUDIT.md) 记录过一个有代表性的边界：运行没有出现 `RTL-8029` probe marker，public QEMU attempt 被保留为 `FAIL/INCONCLUSIVE`，没有把它改写成 PASS。该结果只能触发 artifact/probe/实现归因和窄修复，不能由一次 QEMU 启动或容器退出码推导出驱动成功。

## 4. 问题二：Asterinas 前置能力缺失时如何迁移

### 4.1 先判断“缺失”的类型

“Asterinas 不支持”不是足够精确的诊断。应按下面顺序分类：

| 观察到的缺口 | 应回到的工作 | 是否可以先继续其他实验 |
|---|---|---|
| 源 C 文件、头、生成配置、测试、硬件手册或 revision 缺失 | `evidence_closure` | 可以，先补受控材料；已有证据支持的合同可继续 |
| C 编译参数、预处理分支、布局、ABI、volatile/effect、回调目标未知 | `migration_contracts` | 可以，先做有界结构化探测；不能按名称猜 |
| Asterinas API 定义、调用点、初始化顺序、相似驱动或 artifact 路径未知 | `target_platform_study` | 可以，直接检查冻结目标源码并修复 KB；不能把搜索为空当作能力不存在 |
| 已确认 Asterinas 有扩展点，但模块尚未接入 registry、manifest、component、device table 或 test entry | 在 `driver_implementation` 记录最小 `integration wiring`，由 `artifact_preparation` 验证进入产物 | 可以，先跑 source baseline/QEMU model smoke |
| 代码和包装正确，但没有 target runner/image/injection/repack 路径 | `environment_recovery` 或 `artifact_preparation` | 必须继续尝试官方 runner、SDK、CI、image injection 和 model smoke |
| QEMU 没有设备、事件或注入/观察接口 | `public_qemu_validation`，标为 `QEMU_MODEL_BLOCKED` | 可以，保存可独立验证的 target/source/model 证据 |
| 目标确实没有实现该机制，且需要广泛改 ABI、安全模型、allocator、scheduler 或 IRQ/memory 行为 | `BLOCKED_TARGET_CHANGE` | 只做不依赖该改动的合同和边界实验，必要时请求用户确认 |

最重要的规则是：缺少材料时补证据；缺少目标事实时研究目标；缺少集成入口时先做 wiring；只有现有扩展点确实不能实现已确认合同时，才提出目标 API/framework 改动。

### 4.2 三级目标改动策略

原版 [`target-changes.md`](/home/unix/file/C2R-Driver/C-kernel-to-Rust/skill/knowledge-guided-driver-port/references/target-changes.md) 要求按最低变更级别处理：

#### Level 1：driver-owned change

优先只新增或修改迁移拥有的内容：

- 新的 Rust driver/module 文件；
- driver-local 测试、fixture、harness；
- 模块本地 manifest/config；
- 由现有公开 API 可以表达的资源、IRQ、DMA、网络/块/串口适配。

如果 Asterinas 已有相似驱动的注册、资源和生命周期入口，应该复用这个扩展点，不修改内核框架。

#### Level 2：最小 integration wiring

只有为了让 Level 1 模块真正进入 Asterinas 才修改：

- registry、workspace manifest、build rule；
- device table、component selection；
- image/package list、initramfs/overlay；
- 测试入口和 runner 配置。

这类改动改变的是“选择/打包/入口”，不应顺便重写框架语义或做无关清理。

#### Level 3：target API/framework change

只有当现有 Asterinas API 和 Level 2 wiring 无法正确表达已确认合同时，才允许新增或修改目标框架/API。需要：

- Asterinas 固定 revision 的原始定义、调用点和相似实现证据；
- 明确的 driver contract ID 和 observed blocker；
- 已考虑的 module-local、registry、manifest、component 或包装替代方案及其不足；
- 精确文件、符号、最小行为影响；
- public API/ABI、安全、并发、生命周期和兼容性影响；
- 验证计划和 rollback 方法。

如果变更会影响 public ABI、安全模型、allocator、scheduler、广泛 IRQ/memory 行为或多个无关子系统，不能在普通迁移中直接实现，应标记 `BLOCKED_TARGET_CHANGE`，并把证据、最小方案、风险和所需用户决策写清楚。

### 4.3 必须填写的 necessity record

修改任何已有 Asterinas 文件前保存如下记录：

```text
change_id
driver_contract_ids
problem_and_observed_blocker
target_evidence
alternatives_considered_and_why_insufficient
selected_change_level
exact_files_and_symbols
smallest_expected_behavioral_effect
public_API_ABI_or_safety_impact
validation_plan
rollback_method
status
```

这里的 `target_evidence` 必须指向固定 revision 的原始行号、定义、调用点或文档页；“Linux 有这个 API”“某个名字看起来相似”都不是证据。应保持未修改的 Asterinas baseline、dirty-state fingerprint 和 baseline 运行结果，目标改动最好单独保留为本地 patch/commit，以便回滚和比较。

### 4.4 翻译策略：保持硬件合同，替换 Linux machinery

翻译不是把 Linux 的对象名逐字替换成 Rust 或 Asterinas 名字。应把每条源路径分成三层：

1. 必须保持的硬件行为：寄存器顺序、宽度、页/Bank、reset、DMA、ring、IRQ、长度和错误恢复；
2. Linux 用来表达这些行为的 machinery：模块宏、bus framework、spinlock、workqueue、allocator、error code、callback 和生命周期对象；
3. Asterinas 中实际可证明的机制：目标 trait/type、component registration、资源/中断/DMA wrapper、锁和 deferred work、错误和打包路径。

只有第二层应被替换；第一层由硬件证据和迁移合同约束，第三层必须来自 Asterinas 原始定义、调用点和相似驱动。原版 `translation.md` 特别要求对寄存器、资源、descriptor ownership、TX/RX、IRQ、锁、错误、注册和 cleanup 建立显式 Rust 状态与 invariant，并把每个 `unsafe` 操作绑定到局部指针有效性、对齐、生命周期、排他性、顺序和硬件证据。

### 4.5 Asterinas 缺失能力时的具体决策树

可以按以下顺序执行：

```text
缺失前置能力
  |
  +-- 是材料/版本/原文缺失？--> evidence_closure，补材料和 provenance
  |
  +-- 是 C 语义事实缺失？----> migration_contracts，做有界编译/布局/effect 探测
  |
  +-- 是目标 API/调用链未知？-> target_platform_study，读 Asterinas 原文和相似驱动
  |
  +-- 现有 API 足够，仅未进入镜像？--> driver-owned + integration wiring
  |
  +-- QEMU 无法注入/观察？----> 保留意图，QEMU_MODEL_BLOCKED；修 harness 不改驱动
  |
  +-- 确实没有机制且改动局部？--> necessity record + 最小 target API change
  |
  +-- 需要广泛改变 ABI/安全/调度/内存/中断？--> BLOCKED_TARGET_CHANGE
```

在目标路径未恢复前，可以运行 source baseline、直接 QEMU device-model/qtest/QMP smoke 或静态/合同检查。这些结果用于缩小不确定性，但必须明确它们不是 `MIGRATED_DRIVER_RUNTIME_READY`。原版环境恢复要求至少达到 `EXPERIMENT_READY`；只有证明当前迁移驱动进入 target artifact 并实际执行，才能声明 migrated-driver runtime。

### 4.6 每个目标改动之后的验证顺序

每一项保留的 Asterinas 改动都要依次执行：

1. 与目标改动直接相关的最窄 target check；
2. 该驱动对应的构建/静态检查和设备测试；
3. 受影响的既有 target regression；
4. 通过 artifact identity 证明运行路径经过该改动的 QEMU ladder；
5. 与未修改 baseline 比较行为、日志、计数器和失败模式；
6. 确认 necessity record、影响范围和 rollback patch 仍然准确。

诊断性改动、过时的 target API 试验和临时 fault hook 在最终报告前移除或单独标记。最终应单独列出每个已有 Asterinas 文件的变更、合同、风险、验证、回滚方式和未解决影响。

## 5. 问题三：如何做到每个阶段合理验证

### 5.1 统一阶段门结构

每个阶段都应该声明以下内容：

| 字段 | 作用 |
|---|---|
| 输入快照 | 阶段实际允许读取的上游 revision、artifact、规则摘要和当前 attempt |
| 职责 | 本阶段负责回答的一个边界问题 |
| required outputs | 缺一不可的结构化/文件产物 |
| auxiliary evidence | Prompt、事件日志、命令输出、诊断等辅助材料，不能补齐 required output |
| validator | 程序检查 schema、哈希、依赖绑定、引用、范围、状态和内部一致性 |
| evidence status | 每个结论是 `VERIFIED/INFERRED/PLANNED/...` 哪一种 |
| execution status | 实验是否真的执行以及结果 |
| PASS 条件 | 只针对本阶段，不向后传播更强结论 |
| repair target | 失败应回到哪个最小责任阶段 |
| sealing rule | 当前大阶段是否已封存，能否自动回退 |

模型只能把报告和脚本写入工作区，再通过 `dpf codex submit` 提交；状态、CAS、hash、receipt、ledger 和阶段结果由程序处理。这样可以阻止模型用聊天中的 JSON、`PASS` 字样或改写旧报告来创建不存在的证据。

### 5.2 DPF 18 个阶段的逐阶段验证表

下表是开发模式的实际阶段边界。它描述“该阶段应该验证什么”，不是要求每一行都启动一个独立模型。

| # | 阶段 | 阶段职责 | 合理的验证门 | 失败归属 |
|---:|---|---|---|---|
| 1 | `project_init` | 固定项目、角色、模式和控制配置 | manifest 字段、模式兼容性、规则版本、空项目初始化可复现 | 静态控制器；不通过则不进入 intake |
| 2 | `request_intake` | 保存用户原始请求和 source/target/driver 三项输入 | 原始请求不可变、必填字段存在、没有把后续推断当用户输入 | intake 记录 |
| 3 | `driver_candidate_resolution` | 轻量确定唯一驱动候选 | 候选来自允许的本地元数据；记录设备族、总线、区分依据；未确认前不 clone 大材料 | 候选解析 |
| 4 | `scope_confirmation` | 确认设备、总线、架构、包含/排除范围 | 一次合并问题或明确唯一答案持久化；重启不重复询问；冲突重新打开此门 | 范围确认 |
| 5 | `migration_envelope_freeze` | 冻结迁移边界、版本选择策略、artifact mode、QEMU 路线和完成标准 | envelope 与 intake/candidate 一致；后续不得静默改变身份或 scope | envelope；重大改变需显式 reopen |
| 6 | `repository_acquisition` | 获取并固定 Linux、Asterinas、QEMU 和初始材料 | 完整 commit/revision、只读 upstream、独立 target worktree、source identity、baseline hash 和 acquisition receipt | 获取阶段；保留下载失败和错误证据 |
| 7 | `evidence_closure` | 形成 source/target/QEMU/hardware/test/tooling 六域最小材料闭包 | 每份材料有 origin/version/hash/provenance；coverage/gap/retrieval ledger 可追溯；缺口不是一句“资料不足” | 原文材料回第 7 阶段 |
| 8 | `environment_recovery` | 找到可运行 artifact mode 和实验入口 | 真实执行一次相关 source/target/model/QEMU route，得到输出、退出码或有界 timeout，达到 `EXPERIMENT_READY`；记录所有尝试 | 环境/runner/入口；不可把文档缺失直接 BLOCKED |
| 9 | `knowledge_base` | 建立/验证只读 KB、query contract 和项目 KB Skill | integrity/status、search、exact show、原文件验证、rebuild 可用；目标专项 probe 缺口有记录；KB 不以高文档数冒充语义覆盖 | KB 基础设施或回第 7 补语料 |
| 10 | `target_platform_study` | 固定 Asterinas profile、API evidence、相似驱动完整链路和 target-change plan | 每个目标 API 有定义、调用点、上下文、ownership、error、safety 和版本；至少一条 registration→cleanup→artifact 链路；未知项有调查或 necessity record | 目标事实/API/调用链回第 10 |
| 11 | `migration_handoff` | 把前置证据绑定成下游不可变 handoff | handoff 绑定当前 attempt 的 upstream artifacts、artifact mode、QEMU route、target profile、KB status、source tests、target changes 和 gaps；不读取过期历史替代当前依赖 | 交接绑定错误；修正静态组装，不重做上游 |
| 12 | `migration_contracts` | 完成源码闭包、C 语义事实、硬件/源/目标/QEMU 合同和测试来源 | 每个行为有 source span/structured fact/contract/oracle；测试有 taxonomy/provenance；缺失事实为 `BLOCKED` 而非猜测；合同与 target evidence 一致 | 源材料回 7，目标事实回 10，C/ABI/effect/合同/测试断言回 12 |
| 13 | `analysis_review` | 实现前独立审查研究、分析、合同和测试计划 | reviewer 一次完成指定范围并返回全部实质问题；每条意见引用审查报告行和冻结原文行；明确 PASS、最早责任阶段或真实 BLOCKED | 回 7/10/12；不能直接改代码 |
| 14 | `target_framework_enablement` | 实现合同确认的最小目标框架/API 能力并单独封存 | necessity、直接目标证据、替代方案、安全影响、回滚、目标检查和 change inventory 一致；不改驱动、不引入 fallback/双路径 | 目标框架能力或快照漂移回 14；不把能力缺口推给驱动阶段 |
| 15 | `driver_implementation` | 按合同生成 Rust、适配公开测试、完成必要最小改动和自检 | 只消费第 14 步快照；source coverage、unsafe obligations、target-change inventory、测试 provenance、changed-file snapshot、编译/静态检查证据一致 | 源代码/实现问题回 15；目标框架问题回 14 |
| 16 | `artifact_preparation` | 编译、注入、包装、打包并证明当前驱动进入产物 | base/payload/final hash、insertion/presence proof、variant、入口、runner 和最终 artifact 可复现；构建命令成功但身份不明不能 PASS | packaging/image/entry/identity 回 16；代码错误回 15 |
| 17 | `public_qemu_validation` | 运行公开 harness 并保存不可覆盖 receipt | 运行前计划、唯一 run、进程所有权、artifact identity、每级 ladder oracle、stdout/stderr/serial/capture、expected-vs-actual 和归因齐全；失败 attempt 永久保留 | 未改 artifact 的 harness/oracle 回 17；源码回 15；包装回 16；QEMU 能力标 `QEMU_MODEL_BLOCKED` |
| 18 | `final_evidence_review` | 独立审查最终代码、目标框架、产物、冻结 contract/test oracle 和原始运行证据 | 只核对交付阶段证据；只接受可追溯的 PASS/FAIL/INCONCLUSIVE/BLOCKED；所有实质问题集中反馈，按最小影响阶段路由；通过不等于盲测通过 | 按交付问题回 14/15/16/17；不重新审查或静默重开已封存设计阶段 |

### 5.3 哪些是程序化、单 AI 和双 AI 验证

这里的“AI 验证”必须严格定义：

- **程序化验证（P）**：由控制器、validator、脚本、数据库账本、哈希、CAS 或运行 receipt 根据确定性规则完成。它适合验证文件是否存在、schema 是否正确、依赖和 hash 是否绑定、命令是否执行、退出码和输出是否保存、阶段顺序是否允许；它不能理解一个寄存器映射、测试 oracle 或 Rust unsafe 设计是否语义正确。
- **单 AI 验证（A1）**：一个工作 AI 负责研究、设计、实现、测试适配、失败归因或自检，随后由程序检查它提交的产物。工作 AI 的自检仍属于同一个判断来源，不能称为独立复核。
- **双 AI 验证（A2）**：工作 AI 先产生材料，另一个独立审查 AI 在不同会话、独立角色和只读输入边界下重新判断。审查 AI 必须能够否决、要求回退或标记阻塞；把同一 AI 的追加回复、同一会话的自我纠错或程序 checker-decision AI 视为第二个独立审查者都不成立。

DPF 中的 `DEVELOPER_EVIDENCE` 不是“每个阶段都双 AI”。它采用成本和质量的分层组合：机械事实尽量交给程序，必要的语义工作交给一个 worker AI，只有设计封存前和交付结束后设置独立审查 AI。具体分布如下：

| 阶段 | 主要验证类型 | 是否有 AI | 是否构成独立双 AI | 具体说明 |
|---:|---|---|---|---|
| 1 `project_init` | P | 无 | 否 | 控制器验证项目 manifest、模式和控制配置。 |
| 2 `request_intake` | P | 无 | 否 | 保存原始请求，验证必填输入和不可变记录。 |
| 3 `driver_candidate_resolution` | P | 通常无 | 否 | 通过轻量本地元数据和候选解析确定驱动；不让 AI 在未确认时自行选择大范围候选。 |
| 4 `scope_confirmation` | P + 用户门 | 无工作 AI | 否 | 唯一候选可由程序确认；有歧义时由用户回答一次合并问题。用户决策不是第二个 AI。 |
| 5 `migration_envelope_freeze` | P | 无 | 否 | 程序冻结身份、范围、排除项和控制配置。 |
| 6 `repository_acquisition` | P + A1 | 有时有 | 否 | AI 只提出版本/仓库选择或证据；程序负责 fetch/reuse、commit、来源和 hash。版本选择的语义判断仍是单 worker 来源。 |
| 7 `evidence_closure` | P + A1 | 有 | 否 | worker 选择 source/target/QEMU/hardware/test/tooling facet；控制器获取原文、哈希、provenance、coverage 和 gap。程序检查完整性，但不替代证据相关性判断。 |
| 8 `environment_recovery` | P + A1 | 有 | 否 | worker 设计恢复路线和 smoke 脚本；控制器执行脚本并记录 `EXPERIMENT_READY`、退出码、超时和运行身份。 |
| 9 `knowledge_base` | P | 通常无 | 否 | 控制器构建/检查 status、query contract、search/show/rebuild 和 readiness；目标语义 probe 由第 10 阶段 worker 负责。 |
| 10 `target_platform_study` | P + A1，随后在 13 被 A2 审查 | 有 | **与第 13 阶段合计是** | worker 生成 Asterinas profile、API evidence、相似驱动链路和 target-change plan；程序检查结构和引用，独立 reviewer 在第 13 阶段重新检查这些内容。 |
| 11 `migration_handoff` | P | 无 | 否 | 控制器按当前 attempt 绑定上游 artifact、KB、环境、target study 和缺口；不重新判断技术语义。 |
| 12 `migration_contracts` | P + A1，随后在 13 被 A2 审查 | 有 | **与第 13 阶段合计是** | worker 形成 source closure、C 事实、迁移合同和测试矩阵；程序检查 bundle、依赖和 schema，独立 reviewer 检查合同和测试来源。 |
| 13 `analysis_review` | A2 + P | 有独立 reviewer | **是** | reviewer 同时审查第 10、12 阶段材料，必须一次反馈全部实质问题、精确行号和回退阶段；程序保存输入 digest、规则摘要和审查结论。 |
| 14 `target_framework_enablement` | P + A1 | 有 | 否（直到第 18） | worker 实现并检查目标框架能力；程序验证 target-framework bundle、change inventory、文件哈希和 report 输入绑定。 |
| 15 `driver_implementation` | P + A1 | 有 | 否（直到第 18） | worker 只消费第 14 步快照，编写 Rust、测试适配和合规自检；程序验证快照与 changed paths。 |
| 16 `artifact_preparation` | P + A1 | 通常有 | 否（直到第 18） | worker 准备 artifact 和 presence checker；控制器实际构建/注入/检查 base、payload、final identity。 |
| 17 `public_qemu_validation` | P + A1 | 有 | 否（直到第 18） | worker 编写 harness、声明 oracle、解释 receipt；控制器执行 QEMU、冻结不可覆盖 run 和原始日志。 |
| 18 `final_evidence_review` | A2 + P | 有独立 reviewer | **是** | reviewer 从原始需求重新检查第 14–17 阶段的目标框架、代码、artifact、测试和原始 QEMU 证据；程序验证输入仍是当前 artifact、receipt 和规则摘要。 |

按类别直接汇总：

- **程序化为主**：第 1、2、3、5、9、11 阶段；第 4 阶段是程序化确认加用户确认门。它们不需要工作 AI 来做技术语义判断。
- **单 AI + 程序**：第 6、7、8、10、12、14、15、16 阶段。一个 worker AI 负责相应的语义工作，程序负责确定性验收；这些阶段自身没有独立第二 AI。
- **双 AI + 程序**：第 13、18 阶段。第 13 阶段把第 10、12 阶段的 worker 结论交给独立 reviewer；第 18 阶段把第 14–17 阶段的 worker 结论、目标框架、代码、产物和运行证据交给独立 reviewer。

所以设计上的双 AI 覆盖面是：

```text
目标研究/合同/测试计划：worker（10、12） -> independent reviewer（13）
实现/产物/QEMU 运行：worker（14、15、16） -> independent reviewer（17）
```

第 13 阶段的 reviewer 会在第 18 阶段复用同一个审查会话，但它始终与 worker 会话和职责分离；这仍然是两个 AI 角色，不是三个 AI。复用会话是成本控制，不会把 worker 的自检升级成独立复核。

因此可以用下面的简化图向汇报对象解释：

```text
程序门禁（所有阶段）
  ├─ 文件/schema/hash/依赖/状态/receipt/进程/运行身份
  └─ 不负责判断驱动语义

worker AI（第 6、7、8、10、12、14、15、16 阶段按需参与）
  └─ 研究、设计、实现、测试适配、harness 和归因

独立 reviewer AI
  ├─ 第 13 阶段：重新审查目标研究 + 合同 + 测试计划
  └─ 第 18 阶段：重新审查目标框架 + 代码 + artifact + 测试 + QEMU 证据
```

### 5.4 三种验证的边界和组合规则

三种验证不能互相冒充：

| 组合 | 可以得出的结论 | 不能得出的结论 |
|---|---|---|
| P 单独通过 | 产物结构、身份、依赖、运行收据或阶段协议满足机器规则 | API 映射、寄存器语义、测试意图、unsafe 安全性或驱动功能正确 |
| A1 + P | 一个 worker 的技术判断有格式、来源和执行事实约束 | 没有独立第二 AI 的设计/实现语义复核 |
| A2 + P | worker 的材料经过独立 reviewer 的语义挑战，并且输入/结论可追溯 | 真实硬件、未运行的测试、QEMU 未建模行为或超出 reviewer 输入的事实 |
| source baseline + A2 | 源实现和迁移设计/结果被分别检查 | 自动证明目标运行时等价或真实硬件兼容 |

阶段 `PASS` 仍然只表示本阶段门禁通过。例如第 12 阶段的 `A1 + P` 通过，只表示合同材料完整且 worker 自检被接受；必须经过第 13 阶段 `A2` 才能进入目标框架和驱动实现。第 17 阶段即使程序 receipt 显示 QEMU 正常退出，也只有在预声明的外部 oracle 满足时才可把对应运行记为 PASS；第 18 阶段的 `A2` 可以否决前面的单 AI 自检和程序运行结论。

程序中的临时 `checker-decision AI` 只用于解释机械验收异常或已有失败记录。它没有阶段产物所有权，不能创建缺失输出，也不属于独立功能审查；否则会把“程序规则有异常”和“驱动语义经过第二 AI 复核”混为一谈。

### 5.5 第 12、13、16、17 阶段尤其容易误判

#### 第 12 阶段：合同不是实现通过

第 12 阶段可以确认“需要保持哪些硬件和软件行为、如何观察、哪些测试来源有效”，但不能确认 Rust 实现正确。`VERIFIED` 的源布局事实、目标 API 事实和合同 oracle 仍要和 `implementation`、`artifact`、`QEMU` 的执行状态分开。

#### 第 13 阶段：审查设计，不审查不存在的代码

分析审查应检查 target profile、API 原文和调用点、source closure、C 语义/布局/ABI/effect、合同、测试刺激/断言来源、target-change necessity 和 QEMU 边界。它不能因为 worker 的自检写了“完成”就放行，也不应把“实现尚未开始”本身当成缺陷。审查发现多处问题时应完成整个指定范围后一次性返回所有实质问题，并为每条问题提供：

- 审查报告中的精确行号；
- 冻结 source/target/hardware/QEMU 原文的路径、版本和行号/页码；
- 最小相关片段；
- 为什么这违反合同或证据规则；
- 影响哪个后续合同/测试；
- 最小修复和验证方法。

当前 DPF 还提供了只读 `review_evidence` Python locator：它读取指定 Git commit 的原始 blob、返回编号原文和 hash，并明确把 `LOCATED` 与语义裁决分开，避免程序用模糊匹配替审查 AI 做语义判断。

#### 第 17 阶段：receipt 是运行事实，不是乐观解释

控制器执行 harness、记录进程和输出、冻结不可覆盖 receipt；worker 只能依据该 receipt 解释 oracle 和归因。失败 run 不得被后续追加文字、`ACCEPT` 或改写状态提升为 PASS。`ACCEPT` 只表示工作者接受当前候选进入后续检查，不是把失败测试变成成功。

#### 第 18 阶段：最终审查要回到原始功能

最终审查应同时看原始需求、实际 Rust/target diff、最终 artifact 身份、公开测试矩阵、QEMU 原始日志和所有失败 attempt。它不能把 source baseline、model-only、driver log、compile success 或“未执行测试”合并为迁移驱动 PASS。若失败原因是目标能力或 QEMU 模型，报告应保留 `BLOCKED` 类别，不把它写成 driver bug；若是代码/包装/harness 问题，才回到对应交付阶段做窄修复。

### 5.6 大阶段封存与回退验证

开发模式的 18 个阶段实际跨越三个执行大阶段；控制器另外定义了一个只在盲测封存/完成审计流程中使用的 `completion` 组：

1. `scope_and_baselines`：1–6；
2. `evidence_and_design`：7–13；
3. `delivery`：14–18；
4. `completion`：盲测候选封存后的 `completion_audit`，不属于普通 `DEVELOPER_EVIDENCE` 的 18 个阶段。

自动回退只能在当前尚未封存的大阶段内进行。进入 delivery 后，不能因为实现遇到问题就静默重开已经封存的 evidence/design；需要改变冻结前提时，必须有显式 `phase-reopen`，记录原因、影响、旧证据和新 attempt。阶段历史以 ledger 和 artifact occurrence 保存，不能通过重启、把状态写回 `PENDING` 或修改旧报告绕过封存。

回退目标要按实际责任选择：

- 原始文件/文档/来源/hash 缺失 → 7；
- Asterinas API、调用链、初始化顺序、目标 change 证据缺失 → 10；
- C 编译/预处理/布局/ABI/effect、合同或测试断言来源缺失 → 12；
- 目标框架能力/目标快照 → 14；
- Rust 源码/unsafe/实现 coverage → 15；
- 镜像、入口、payload、artifact identity → 16；
- 未改变 artifact 的 harness/oracle → 16。

不能把所有问题都退回第 7 阶段，也不能把“后续没有通过”误解为允许跨大阶段回退。回退理由必须写出观察、责任边界、所需输入变化和受影响的重新验证集合。

### 5.7 阶段验证的成本控制

为了兼顾成本和质量，建议保持以下边界：

- 静态阶段由控制器完成，不为机械 hash/schema/依赖绑定启动模型；
- 第 12 阶段合并源码分析、合同和测试计划，避免同一份上下文重复加载，但必须用完整 bundle validator 保证三部分都存在；
- C 语义采用问题驱动的最小编译/预处理/布局/effect 探测可以节约成本，但每个影响合同的疑点必须有探测、直接证据或明确 blocker；不能因没有全量 AST/CPG 就允许猜测；
- 分析审查和最终审查一次集中反馈全部实质问题，复审只读取修改及影响范围；
- 每个成功 receipt、CAS artifact 和未改变输入可以复用；报告补充文字不能触发新的 QEMU；代码、harness、镜像或规则摘要变化必须创建新 attempt/run；
- 测试分类只在冻结 source closure 中提供候选 inventory，具体分类、断言、适配和排除理由仍由模型核对原文；
- 失败归因先修复最小责任层，再重跑受影响测试和必要回归，避免每次问题都从头构建所有材料；
- 同一实质输入重复失败应持久化停滞并暂停，只有输入变化或显式外部恢复原因才能继续。

### 5.8 当前 DPF 能力边界

根据 [`SKILL_TRACEABILITY.md`](SKILL_TRACEABILITY.md)，当前状态应如实表述：

**已实现或已有确定性骨架的部分：**

- intake ambiguity persistence；
- 真实 `EXPERIMENT_READY` 环境门；
- target platform study 的 profile/API/analogous trace 门；
- 17 阶段的依赖、required/auxiliary artifact、CAS/ledger 和 phase sealing 边界；
- 模型写文件并通过 tool submission 改变状态；
- 第 13 阶段独立分析审查和第 18 阶段独立最终审查的协议；
- 失败 attempt 不覆盖、run ID 和基础执行恢复。

**仍需谨慎称为 `PARTIAL` 或 `PLANNED` 的部分：**

- 完整 revision/baseline isolation；
- 六域 evidence closure 和 handoff 完整性；
- knowledge-base completeness 与全量 target probe；
- C source closure、结构化语义事实和 migration contracts 的完整覆盖；
- 测试 provenance 和所有源测试的确定性适配门；
- artifact insertion proof；
- 完整十级 QEMU evidence ladder、公开失败窄修复和 final contract audit。

因此，当前流程可以说“已经能强制执行主要边界和保存证据”，不能说“所有原版 Skill 的质量门已经自动实现”。报告、实验结果和对外汇报都应该按上述追踪状态表述。

## 6. 推荐的交付报告格式

最终每个驱动项目应至少交付以下互相引用的材料：

1. intake、scope、revision 和 acquisition manifest；
2. 六域 materials manifest、coverage、gap 和 retrieval ledger；
3. environment recovery、artifact mode、`EXPERIMENT_READY` run；
4. KB status/query/show/rebuild 命令和 target-specific probe 结果；
5. Asterinas target profile、API evidence table、相似驱动端到端 trace；
6. 每个 pre-existing target change 的 necessity record 和 rollback patch；
7. source closure、结构化 C facts、硬件/源/目标/QEMU contracts；
8. source-test inventory、taxonomy、mapping records 和 provenance；
9. `analysis_review` 的完整问题清单、精确引用和修复复审记录；
10. Rust implementation snapshot、translation coverage、unsafe obligations、compliance 和 changed-file inventory；
11. artifact base/payload/final identities、driver-presence proof、runner/entry；
12. 每个 QEMU run 的 plan、receipt、原始日志、oracle、expected-vs-actual、status 和 attribution；
13. `final_evidence_review` 的最终独立审查，以及按合同分别报告的 PASS/FAIL/BLOCKED/NOT_RUN。

这套格式使汇报者能回答“验证了什么、用什么证据验证、哪些没有验证、为什么没有验证、下一步需要什么”，而不是只给一个无法审计的迁移成功率。

## 7. 可直接用于汇报的总结

> 驱动验证采用合同驱动和证据分层的方法：先冻结硬件、源平台、Asterinas、QEMU 和测试的输入，再为每个行为定义 stimulus、独立 oracle、清理和结果状态，最后通过 artifact identity 和 QEMU evidence ladder 证明当前迁移驱动确实执行。源测试必须按设备功能、设备协议、可移植意图、源平台语义、设备变体、目标能力和 QEMU 能力分类，不能用文件名或日志代替判断。
>
> 当 Asterinas 缺少前置能力时，首先区分证据缺失、目标事实缺失、集成 wiring、artifact 包装、环境和 QEMU 模型问题；优先使用 Asterinas 已有扩展点，按 driver-owned、最小 integration wiring、必要且有证据的 target API/framework change 逐级处理。只有影响已确认合同且范围最小、可验证、可回滚的目标改动才允许保留；涉及公共 ABI、安全模型、调度器、allocator 或广泛 IRQ/memory 语义时应明确阻塞并请求决策。
>
> DPF 用 18 个阶段把这些技术要求变成可恢复的状态机。每个阶段都有自己的 required output、程序 validator、证据/执行状态、最小回退目标和大阶段封存规则；第 13 阶段独立审查设计，第 14 阶段封存目标框架能力，第 17 阶段冻结真实 QEMU receipt，第 18 阶段独立审查最终代码、目标框架、产物和运行证据。这样可以兼顾成本和质量：静态事实由程序处理，复杂语义集中审查，成功证据复用，失败证据不覆盖；同时对尚未完成的 `PARTIAL/PLANNED` 门禁保持明确披露。

> 按验证主体划分，第 1、2、3、5、9、11 阶段主要由程序完成，第 4 阶段增加用户确认；第 6、7、8、10、12、14、15、16 阶段是一个 worker AI 加程序门禁；第 13、17 阶段是 worker AI 之后由独立 reviewer AI 重新审查的双 AI 闭环。程序验证负责结构、身份、依赖和真实运行事实，AI 验证负责证据语义、合同、实现和归因，二者不能互相替代。

## 8. 主要依据

- 原版入口和环境恢复：[`open-kernel-driver-port/SKILL.md`](/home/unix/file/C2R-Driver/C-kernel-to-Rust/skill/open-kernel-driver-port/SKILL.md)、[`environment-recovery.md`](/home/unix/file/C2R-Driver/C-kernel-to-Rust/skill/open-kernel-driver-port/references/environment-recovery.md)、[`handoff.md`](/home/unix/file/C2R-Driver/C-kernel-to-Rust/skill/open-kernel-driver-port/references/handoff.md)
- 原版目标研究和翻译：[`knowledge-guided-driver-port/SKILL.md`](/home/unix/file/C2R-Driver/C-kernel-to-Rust/skill/knowledge-guided-driver-port/SKILL.md)、[`target-platform-study.md`](/home/unix/file/C2R-Driver/C-kernel-to-Rust/skill/knowledge-guided-driver-port/references/target-platform-study.md)、[`translation.md`](/home/unix/file/C2R-Driver/C-kernel-to-Rust/skill/knowledge-guided-driver-port/references/translation.md)、[`knowledge-contract.md`](/home/unix/file/C2R-Driver/C-kernel-to-Rust/skill/knowledge-guided-driver-port/references/knowledge-contract.md)
- 原版测试、目标变更和 QEMU：[`test-porting.md`](/home/unix/file/C2R-Driver/C-kernel-to-Rust/skill/knowledge-guided-driver-port/references/test-porting.md)、[`target-changes.md`](/home/unix/file/C2R-Driver/C-kernel-to-Rust/skill/knowledge-guided-driver-port/references/target-changes.md)、[`qemu-evidence.md`](/home/unix/file/C2R-Driver/C-kernel-to-Rust/skill/knowledge-guided-driver-port/references/qemu-evidence.md)
- DPF 阶段与执行协议：[`STAGE_GUIDE.md`](STAGE_GUIDE.md)、[`WORKFLOW.md`](WORKFLOW.md)、[`EXECUTION_RECOVERY.md`](EXECUTION_RECOVERY.md)、[`ANALYSIS_REVIEW.md`](ANALYSIS_REVIEW.md)
- DPF 测试适配和实现边界：[`TEST_ADAPTERS.md`](TEST_ADAPTERS.md)、[`SOURCE_DESIGN.md`](SOURCE_DESIGN.md)、[`SKILL_TRACEABILITY.md`](SKILL_TRACEABILITY.md)
- 公开 NE2000 运行审计：[`E2E_NE2000_RUN_AUDIT.md`](E2E_NE2000_RUN_AUDIT.md)
- DPF 实现入口：[`migration.py`](/home/unix/file/C2R-Driver/c2rust-migration-test-01/driver-port-factory/src/driver_port_factory/orchestration/migration.py)、[`phases.py`](/home/unix/file/C2R-Driver/c2rust-migration-test-01/driver-port-factory/src/driver_port_factory/core/phases.py)、[`artifact_preparation.py`](/home/unix/file/C2R-Driver/c2rust-migration-test-01/driver-port-factory/src/driver_port_factory/migration/artifact_preparation.py)、[`public_qemu.py`](/home/unix/file/C2R-Driver/c2rust-migration-test-01/driver-port-factory/src/driver_port_factory/migration/public_qemu.py)、[`analysis_review.py`](/home/unix/file/C2R-Driver/c2rust-migration-test-01/driver-port-factory/src/driver_port_factory/migration/analysis_review.py)
