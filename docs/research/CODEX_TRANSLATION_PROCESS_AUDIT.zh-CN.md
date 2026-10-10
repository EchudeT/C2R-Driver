# Codex 驱动翻译过程：问题、代码核对与 V3 的对应处理

日期：2026-10-10。本文为[项目演进汇报](PROJECT_EVOLUTION_BRIEFING.zh-CN.md)的过程证据补充。只读取已有日志、提示、源码和评分，不改变候选、实验配置或成绩，不开展新的模型调用或测试。

## 1. 本次深入核对得到什么

现有过程证据支持八项具体观察，远不止“遗漏注册”和“追加检查”。其中有的发生在同一会话，不能当作八次独立实验。

| 编号 | 实际观察的问题 | 案例 | 当次最终情况 | 判断边界 |
| --- | --- | --- | --- | --- |
| P1 | 局部设备实现完成，完整接入链仍缺一环 | Flash RNG、Sol RNG、Sol NVMe | 运行暴露后补接入，相关公开测试通过 | 跨模型、跨驱动出现同类遗漏；不估计发生率 |
| P2 | 驱动与源内核调用者共同提供的行为，最初没有共同迁移 | Sol RNG 的缓存／补充请求时机 | 补目标调用者缓存后通过 | 中间语义遗漏，最终已修复 |
| P3 | 依据接口名称理解数据方向，源与目标的约定实际相反 | Luna Network RX/TX | 两次改正描述符方向 | 具体中间代码错误，不解释该轮全部失败 |
| P4 | 只检查一个 feature，遗漏决定协议布局的另一个条件 | Luna Network 报文头 | 从 10 字节改为 modern 所需的 12 字节 | 由源分支、固定配置与补丁确认 |
| P5 | 新增恢复路径时复用正常写入缓冲，修复引入跨路径错误 | Sol NVMe | 模型发现并分离 Identify DMA，最终修正 | 静态违反路径成立，没有运行故障注入证据 |
| P6 | 自写执行脚本观察错误日志，把服务启动误判成驱动卡死 | Sol Network | 修正观察入口后确认服务一直在运行，最终 3/3 | 测试接入错误，不能归罪于设备实现或 Docker |
| P7 | 修复公开场景所暴露的协议问题后，另一条件分支仍遗漏 | Luna NVMe PRP2 | 公开 64 KiB 通过，私密 8 KiB 失败 | 最终残留缺陷；同批 Factory 也失败，不能算 V3 优势 |
| P8 | 检查对象、执行方式和停止范围由会话继续扩展 | Flash RNG | 最终完成，但追加其他设备检查及重复运行 | 部分检查合理，不能一律当作浪费 |

这些观察共同指向一个迁移问题：**设备逻辑、源内核调用者、目标接口、协议条件、失败恢复和运行证据之间存在必须保持的联系。模型即使具备完整源码和明确要求，也可能在实现与修复过程中暂时丢失这些联系。**

V3 的对应做法，是将已识别的联系放进联合分析、当前完整工作包和知识消费入口，再由固定执行与独立验收承接运行证据。这是可核对的组织差异；“所有遗漏已经消除”不是本次证据支持的结论。

## 2. 怎么查，怎样避免把模型波动当成框架问题

本次定位到 `comparisons/*/*/codex/model-home/sessions/*/*/*/rollout-*.jsonl` 下的 20 份会话，对多个批次的助手过程说明作导航扫描，再对下列 RNG、Network、NVMe 关键轨迹逐条核对工具调用、补丁和反馈。这个样本是已有材料的定向审计，不是随机抽样，也不是 20 份会话的全部动作统计；不据此计算缺陷率或模型请求次数。

V3 机制核对基于当前 Factory 主分支 `53e959c344b09d765394261deb342f15e433fee3`；历史运行身份由各自 trial 的提示、会话和协议确定。较早与较晚批次的范围有调整，不能混成同协议缺陷率统计，也不能把当前代码直接说成所有历史运行使用的代码。

每项结论遵循四步：

1. **先确认过程。** 模型做出什么判断、改了什么、实际反馈是什么。模型说“环境坏了”不等于环境确实坏了。
2. **再确认代码。** 核对当时工具调用中的补丁及保留的源／目标定义。后来修正的文件不能被当作当时仍有错误的文件。
3. **再核对约束。** 读取该轮实际 `PROMPT.md`，确定行为在范围内、必要信息存在且任务没有允许跳过。
4. **最后归类。** 单次执行错误、最终残留缺陷、可核对的组织差异分别陈述。只有控制器确实如何处理，才写成机制结论。

以下链接的行号均指 JSONL **物理行号**，不是模型调用号。目录命名与 UTC 时间可能不同，案例以 trial ID 为身份；诊断时间区间取日志内时间戳。

### 2.1 baseline 本来已经要求什么

已核对以下案例的实际提示：[Luna Network 提示][T-NET-LUNA]、[Sol Network 提示][T-NET-SOL]、[Sol NVMe 提示][T-NVME-SOL]、[Luna NVMe 提示][T-NVME-LUNA]、[Sol 接入案例提示][T-NVME-SCOPE]。它们已经要求：

1. 自主读取完整 Linux、调用者、共享核心和目标定义。
2. 保持范围内语义，核对责任由驱动、框架还是调用者提供。
3. 真正实现并注册设备，不允许假成功、替代设备或把跳过算通过。
4. 公开测试是观察，不是翻译规范，不能只照测试实现。
5. 使用固定 Docker／OSDK／QEMU 路线，最终另行运行固定公开套件。

因此，P1–P5 和 P7 不是“没告诉 Codex 要保持语义”。它们是在已有要求下出现的具体执行偏差。

[baseline 运行配置][M-BASELINE]关闭多 agent、自定义 MCP 与 Factory 规划／知识库；[goal 客户端][M-GOAL]设置原生持久目标，不添加专门的迁移规划或 reviewer 续跑提示。它让模型通过通用工具自主组织调查、实施和检查。该配置没有 Factory 的“源义务—目标路线／前提—承担包”关系索引、当前包投递与修订协调机制。这是本项目 baseline 的组织差异，不能扩展成对 Codex 内部能力或所有使用方式的判断。

## 3. P1：可编译的设备实现，不等于完整接入

### 3.1 实际过程

1. Flash RNG 会话已经读取上层接口，随后实现设备；首次运行仍缺 `/dev/hwrng`，补上层初始化后 15 项通过。具体次序见[原汇报 E1](PROJECT_EVOLUTION_BRIEFING.zh-CN.md#e1codex-rng-原生会话)。
2. [Sol RNG][L-RNG-SOL]第 556 行同样报告：后端已构建，空心树删除的 `hwrng` 模块声明及初始化调用没有恢复，设备节点未创建。
3. [Sol NVMe 接入案例][L-NVME-SCOPE]第 348 行报告：NVMe crate 已编译，但没有内核 assembler 引用，设备节点不存在，回归测试跳过；补 component hook 后第 376 行六项断言通过。

### 3.2 核对与根因

日志明确区分“编译到 crate”与“实际注册、上层可用”，并记录补接入后的运行结果。实际提示已要求真实接入，空心树中的注册位置需要模型自主识别和补齐。这不是缺少输入，也不是编译器失灵。

同类遗漏在 RNG、NVMe 以及不同模型中出现，足以说明它不只是一条 RNG 日志里的特例；仍不足以宣称所有 Codex 任务都会遗漏。成立的动机是：**设备实现、初始化分发和上层发布属于同一个迁移目标，不能靠局部编译状态代表整体完成。**

### 3.3 V3 怎样回应

[实际 V3 RNG 分析][V-RNG]的 R1/P1/C1/B1 提前列出设备发现、初始化分发、`first_device()`、调用者初始化与设备节点发布。B1 负责整个目标，而非只写设备目录。

[关系模型][M-RELATION]检查已声明义务都有承担包，[当前包投递][M-PACKET]将相关路线、义务和前提原段落交给执行者，最后设备专用公开测试验证真实接入。这里确认的是完整目标及其投递机制，不能由单个成功案例证明注册遗漏率为零。

## 4. P2：源行为分散在驱动与调用者中，迁移责任最初没有接齐

### 4.1 实际过程

1. [Sol RNG][L-RNG-SOL]恢复设备节点后，多项测试已通过。
2. 第 717 行指出：一次 1 字节读取直接消费驱动缓冲，留下 63 字节；紧接着要求 64 字节的非阻塞读取失败。模型发现 Linux `hwrng` 共享调用者会先读取完整缓冲，再服务小用户读取。
3. 第 718 行补丁在目标 `hwrng.rs` 增加调用者缓存，将原先按用户剩余长度调用驱动的路径，改为先向缓存取数据，再向用户复制。
4. 第 764 行说明补齐源调用者补充缓冲时机；后续公开测试通过。

### 4.2 源码核对与根因

[Linux `core.c`][S-RNG-CORE]第 258 行起，`data_avail` 为空时先调用 `rng_get_data(..., rng_buffer_size(), ...)`，随后按用户长度消耗缓存；不是每次直接按用户读取长度消耗设备缓冲。第 274–281 行再决定本次复制量及剩余位置。日志补丁与这项源调用者职责一致。

这里的问题与注册不同：设备已经能用，但**端到端读取行为由设备驱动和共享核心共同决定**。把目标现有前端当作天然等价，会遗漏原调用者提供的条件与时机。该轮后来修正，不能写成最终仍有此缺陷。

### 4.3 V3 怎样回应

V3 [联合分析任务][M-ANALYSIS]明确要求读源入口与共享核心，写清驱动、框架或调用者分别保证什么。实际 RNG C4/C5 将等待、非阻塞错误和调用者路径纳入 B1；C2/C3 列出补充请求与完成的设备职责。[实施任务][M-BEHAVIOR]允许在同一包适配调用者，无需等到下一阶段。

这个机制让跨边界职责有持久位置并进入实施。**现有 C4/C5 的存在不等于它已完整表达所有缓存条件**，本案例也不能被写成“V3 已证明预防了 Sol RNG 的相同遗漏”。可证明的处理是责任组织与源自检要求。

## 5. P3：接口名相似，数据方向的观察视角不同

### 5.1 实际过程与补丁

1. [Luna Network][L-NET-LUNA]第 902 行根据 QEMU 反馈，发现 RX 提交用了 `add_input_bufs`，设备无法写入接收缓冲。
2. 第 903 行补丁将 RX 改为 `add_output_bufs`。
3. 第 1045 行继续发现 TX 也反了；第 1046 行补丁将 TX 的 `add_output_bufs` 改为 `add_input_bufs`。

### 5.2 定义核对与根因

[目标 `queue.rs`][S-NET-QUEUE]第 239、246 行定义两种方法；第 289 行起 inputs 描述符不设置 WRITE，第 306 行起 outputs 设置 WRITE，意味着设备可以写。Linux RX 使用 `virtqueue_add_inbuf*`，TX 使用 `virtqueue_add_outbuf*`，可见[源 `virtio_net.c`][S-NET-LINUX]第 2606–2611、564 行。

因此，同样叫 input/output 的接口，两边命名视角不同。代码事实可以直接判定：目标 RX 必须提供设备可写描述符，TX 必须提供设备可读描述符。编译器可以验证参数类型，却不能仅凭这些方法名判定收发责任正确。

这个错误由模型随后发现，不能据此宣称最终 Network 的全部失败都由队列方向造成。成立的迁移动机是：**应对齐设备读写权与提交／完成时机，不能只对应 API 名称。**

### 5.3 V3 怎样回应

V3 分析提示要求“源触发条件、目标承担者、必要前提”，明确接口名或 guard 类型本身不是语义映射。实际 V3 RNG C2 直接说明 `add_output_bufs` 是设备可写，C3 说明提交到完成期间的设备所有权及 DMA 同步。这证明该表达方式已经用于真实迁移，不只是一段口号。

当前包收到原段落，已有解释与原定义可以由知识工具按问题检索。模型仍需正确读取定义；控制器没有自动证明每个 VirtQueue 调用方向正确。

## 6. P4：协议参数取决于组合条件，不能只看一个 feature

### 6.1 实际过程

1. [Luna Network][L-NET-LUNA]第 466、524 行认为：QEMU 禁用 `MRG_RXBUF`，因此应使用 10 字节报文头。
2. 第 2183 行后来发现：冻结配置是 modern-only，Linux 在 `VERSION_1` 下即使不启用合并接收缓冲，也使用 12 字节布局。
3. 第 2184 行补丁将 `VNET_HDR_SIZE` 从 10 改为 12，并补 `num_buffers` 字段。

### 6.2 源分支与配置核对

[Linux `virtio_net.c`][S-NET-LINUX]第 6919–6923 行的条件是 `MRG_RXBUF || VERSION_1`，而非单独 `MRG_RXBUF`。保留的[QEMU 参数文件][S-NET-QEMU]指定 modern 网络设备，并在 feature profile 中关闭 `mrg_rxbuf`。日志中的修正补丁符合这个组合。

本质是**把一个协议条件当成完整判定**。范围明确为 modern 并不能免去源码分支核对；类型和一般收发接口也不能保证线上的字节布局。模型一度确信方案正确，后来因继续诊断才推翻自己的解释。

### 6.3 V3 怎样回应

在 V3 中，这类决定布局的事实应进入源义务或关键前提：固定的版本／feature 条件，采用何种布局，目标谁据此处理。ready 的已声明关键前提需要源码或观察依据，随后直接投递给关联包；按需知识检索补原定义，而非重复调查整个平台。

这说明框架怎样组织该问题，不是声称当前实现具有自动 feature 分支提取器。若分析遗漏 `VERSION_1`，索引检查不能凭空发现它。

## 7. P5：修复恢复路径，可能破坏正常路径的不变量

### 7.1 原始补丁构成的违反链

[Sol NVMe 恢复案例][L-NVME-SOL]中：

1. 第 408 行公开测试六项通过；模型继续处理源错误／恢复语义。这一步本身合理。
2. 第 750 行加入 `reinitialize()`，Identify 命令的 DMA 地址设为 `self.data`，控制器会把识别数据写入该页。
3. 第 757 行加入传输错误后 `reinitialize()` 并 `continue` 重试原 I/O 命令的逻辑。
4. 第 854 行模型发现：这也是正在承载写数据的页，恢复可能用 Identify 数据覆盖它，然后重试错误内容。
5. 第 862 行分配独立 `identify` DMA，并将管理命令改用该缓冲。保留的[最终候选][S-NVME-RECOVERY]中，写路径第 535 行起向 `controller.data` 写数据，管理路径第 338 行使用 `self.identify`，已经分离。

### 7.2 为什么不是泛泛的“可能不安全”

这条路径能明确写为：写数据装入 data 页 → I/O 传输错误 → 控制器恢复在同页执行 Identify → 该页内容被管理命令替换 → 原写命令重试时引用同页。第 750 与 757 行补丁共同提供静态依据；第 862 行是针对同一问题的实际修正。

没有在该轮注入传输故障，因而不能写成“实际观测到磁盘数据损坏”。但已经确认**中间恢复方案违反待重试写数据必须保持的条件**。模型自己后来修复，不能把它计作最终残留缺陷。

### 7.3 V3 怎样回应

V3 把正常操作、必要失败处理与资源生命周期放在同一个完整目标中；分析要求记录所有权、失败及清理的承担关系。同包实现者能直接看见已记录的共享条件，并在编码中核对实际资源关系。重大路线／前提变化通过[路线修订][M-REVISION]更新相关包依据与显式前置关系，而不是把“恢复已实现”当作与写入无关的局部完成。

这不是“不同包完全独立”的论证，也不是自动别名分析。当前包协调可以核对，复杂多包修订的普遍效果尚不能由这个 Codex 单包案例证明。**V3 的优势是显式组织共享条件及影响范围，模型仍需识别真实的数据关系。**

## 8. P6：运行观察不可靠，正确代码也会被错误反馈带偏

### 8.1 实际过程

[Sol Network][L-NET-SOL]是已登记、最终成功的案例，不只是在失败任务中挑异常：

1. 第 1352、1430 行，三个公开脚本已成功；模型看到高 UDP 丢包，继续调查。调查网络质量具有理由。
2. 第 1469 行开始低速诊断；后续将日志停在 `/init` 解释为服务没启动。
3. 第 1822、1915、1923 行附近调整初始化顺序、增加失败清理并回退相关顺序；第 2130 行又尝试早先候选作对照。
4. 第 2191 行终于修改自写 harness：原来只检查 OSDK stdout；现在还检查 `qemu-serial.log`，启动前删除旧串口日志。
5. 第 2227 行明确承认：服务一直在运行，所谓超时是 harness 误读，不能证明驱动停止进展。
6. 第 2254 行低速诊断收发一致；第 2336 行最终公开三项再次通过。已登记私密成绩也为 6/6。

### 8.2 环境与代码核对

[自写 `run_public_case.sh`][S-NET-HARNESS]第 27 行现在同时检查两个日志；补丁第 2191 行保留了原来只读 stdout 的版本。[目标 `tools/qemu_args.sh`][S-NET-QEMU-SOL]第 71 行起，在默认 hvc0 配置下把串口重定向到 `qemu-serial.log`，所以误读有明确机制解释。

这是自写接入脚本与上游输出约定不一致，不能说成“Docker 环境不可用”，也不能把 `/init` 之后没有 stdout 当成内核卡死。期间新增的失败清理可能有独立价值，不能把所有代码修改都判成错误。

第 1469 至 2227 行时间戳为 `19:23:29.258Z` 至 `19:43:56.072Z`，相隔约 **20 分 27 秒**。这个区间包含正常诊断、错误观测、代码调整与构建；不能把每一秒都归因于错误日志，也不能把它全部减去作为“可节省时间”。

### 8.3 V3 怎样回应

Factory 提供固定原生执行入口。[Docker 配方][M-NATIVE]显式设置 `CONSOLE=ttyS0`；[原生运行器][M-RUNTIME]让客体操作、ready 等待和实际反馈使用约定的串口通道，Network benchmark 等待固定 runner 的 ready 标志；模型通过受控调用取得执行结果及失败日志。

这直接减少每轮重新拼装日志观察与测试启动器的负担。它不只是多给一段提示，而是把环境执行与观察连接放进程序。baseline 按已批准协议使用上游命令、自写脚本，最终由同一外部套件评分；这项优势属于完整系统比较，不能单独归因于语义分析创新，也不能声称程序能排除所有观测错误。

## 9. P7：公开场景修复成功，协议另一分支仍可遗漏

### 9.1 从过程到最终残留代码

1. [Luna NVMe][L-NVME-LUNA]第 1933 行公开 64 KiB 直接写失败，控制器返回 PRP 错误，六项断言中只有三项通过。
2. 第 1938 行模型定位为每个 Bio segment 只放一个地址，没有枚举每个 DMA 页；第 1953 行修正。
3. 第 2052 行六项公开断言通过。
4. [最终 `submit_io`][S-NVME-PRP]仍在 `pages.len() > 1` 时一律让 PRP2 指向地址列表，缺少恰好两个数据页时直接指向第二数据页的分支。
5. 已有私密 8 KiB 评分失败，32 KiB 和公开 64 KiB 通过；Linux/QEMU 定义及封存哈希已在[PRP 诊断][E-PRP]中核对。

### 9.2 结论与归因

这是最终残留的真实协议编码缺陷。页对齐、4 KiB 页配置下，8 KiB 对应“两页直接地址”，更长传输才对应 PRP 列表。修好长请求并通过测试，不足以证明短请求分支正确。

不能据此声称模型故意过拟合测试；行为可能来自条件分支遗漏。更不能将“测试驱动了修复”判成坏事。问题是：**观察到的通过条件，不应替代原源码中其他仍在范围内的条件。**

### 9.3 V3 在这里能主张什么

V3 将源义务与运行观察分别记录，要求末包源自检，禁止将未覆盖义务因测试通过而称作已证明。这是对此类风险的处理机制。

但同批 Factory 候选也遗漏该分支、私密评分同样失败。**本案例支持迁移问题与测试边界的存在，不支持 V3 已解决这个具体遗漏。** 所有候选与失败成绩保留；本文不会把后来机制说明冒充当时的成功证据，也不要求重跑实验。

## 10. P8：执行范围与停止依据继续由会话决定

[Flash RNG 原会话][L-RNG-FLASH]在第 705 行 RNG 已通过后：第 706 行继续其他设备测试，第 720 行多启动命令解析失败，第 758 行普通 clippy 触发目标配置错误，第 765 行转正确路线，第 842 行重复 RNG 三轮。

这些动作一部分属于合理的保留系统回归或稳定性检查，不能直接贴“无用”标签。可确认的是：当前任务、执行入口、回归范围和收尾时机继续由模型逐次组织，既能产生有益检查，也会产生配置试错与扩大执行范围。

V3 将当前包、固定开发执行入口和最终完整公开批次分开管理；通过的最终批次恢复时可复用，不因临时路径与报告不同要求重新执行。提示要求实际失败才修，不能因为补记录不断重跑。这种边界减少开放式收尾，但不是强制拦截模型的每一条 shell 命令。

## 11. 把八项观察收束成 V3 的设计理由

| 从过程得到的问题 | V3 的具体设计 | 实际依据 | 证据支持的主张 |
| --- | --- | --- | --- |
| P1，端到端接入联系断开 | 粗路线与完整工作包共同覆盖发现、注册、调用及清理 | RNG R1/P1/C1/B1；关系校验与投递 | 已组织并投递完整目标，不保证全部接入遗漏消失 |
| P2，源职责跨驱动与调用者 | 联合源／目标分析，义务写目标承担者与条件 | 分析任务；RNG C4/C5 与 B1 | 跨边界责任有持久位置，执行者能直接消费 |
| P3，API 命名不能表达读写权 | 条件化语义映射，按当前缺口检索原定义 | RNG C2/C3；问题证据包 | 已用于实际 DMA 方向与所有权解释，不是自动协议证明 |
| P4，组合条件决定协议 | 关键前提附原文依据，关联路线并投递 | ready 前提检查；当前包 packet | 已声明前提有可查依据，不能保证发现未声明分支 |
| P5，恢复修改影响正常操作 | 生命周期在完整包内，重大改变更新依据和相关依赖 | 分析／实施任务；route 修订与 bases | 共享条件及显式关系参与协调，不假定包天然独立 |
| P6，错误观测带偏诊断 | 预置环境执行、日志通道、固定 suite | Docker 配方与 native runtime | 执行观察由程序承接；是工具及整体系统优势 |
| P7，测试通过不能覆盖全部语义 | 源义务与运行观察分开，源自检与独立私密评分 | 分析／实施任务；已有评分 | 诚实记录验证边界，不能把双方法失败算成 V3 胜出 |
| P8，开放式检查与收尾 | 当前目标、既有观察、最终完整批次和复用规则 | behavior-job；public_qemu | 收尾有确定程序边界，不等于禁止所有额外调查 |

知识库在这些处理里承担核心的**信息消费与积累**职责：已接受分析并非一次性报告；[问题证据包][M-KB]可联合检索任务报告和相关源／目标原文；[分析经验发布][M-KB-ROUTE]与[实施经验][M-KB-LEARNING]保存选定、带条件和来源的发现，供以后工作者检索。直接包投递传递本轮必须承担的义务，知识检索解决当前缺口，两者相互补充。

本次对照关闭外部共享经验库，所以不能把成本差异解释为跨驱动积累的实测收益。当前代码证明存储、查询、适用条件和发布链路存在；“后续模型实际取用多少、减少多少重新研究”不能由保存成功本身推得。

适合汇报的核心动机是：**驱动迁移要求保持一组跨边界关系；通用 agent 能完成和修复，但这些关系常在反馈后才被重新接齐。Factory 将已识别关系持续交给当前目标，并用知识和固定执行承接发现与观察。** 费用与时长是这一设计的结果指标，而不是唯一动机。

## 12. 反例与不能使用的说法

1. **“Codex 根本不看源上下文。”不成立。** 实际提示提供完整 Linux，日志有源及调用者调查。应该讨论已读事实怎样落实，而非虚构输入被裁剪。
2. **“公开测试通过后继续修改都是浪费。”不成立。** Sol NVMe 第 376 行之后继续错误处理和 DMA 清理具有源义务依据；其他会话的 CQ 发布顺序、namespace 处理等也不能因测试已通过而否定。
3. **“模型说卡死，就是内核卡死。”不成立。** Network 错误日志观察给出明确反例。
4. **“出现中间错误等于最终代码差。”不成立。** P1–P6 多项随后修正，必须分别记录过程开销和最终质量。
5. **“V3 有关系索引，全部源语义必然覆盖。”不成立。** 索引能检查已声明关联，不能证明模型发现了全部义务。P7 是不能掩盖的证据边界。
6. **“这八类问题是八个独立缺陷率样本。”不成立。** 部分来自同一会话，批次和范围也有变化，只作过程解释。

## 附录：原始核对入口

下列为原始文件路径。正文引用的物理行号可直接用于日志定位；无需启动实验或运行模型。

| 核心案例 | trial ID | 原会话 | 主要定位行 |
| --- | --- | --- | --- |
| 注册与源调用者缓存 | `gpt6-sol-medium-20261008-01`，RNG | [会话][L-RNG-SOL] | 556、717、718、764 |
| NVMe 编译但未接入 | `gpt6-sol-medium-scope-20261009-05`，NVMe | [会话][L-NVME-SCOPE] | 348、376 |
| 方向和协议条件 | `wavepulse-unlimited-20261006-net-01` | [会话][L-NET-LUNA] | 466、524、902、903、1045、1046、2183、2184 |
| 错误日志观察 | `gpt6-sol-medium-scope-20261009-05`，Network | [会话][L-NET-SOL] | 1469、1822、1915、2130、2191、2227、2336 |
| 恢复覆盖写数据 | `gpt6-sol-medium-20261009-04` | [会话][L-NVME-SOL] | 408、750、757、854、862 |
| PRP 公开修复与残留 | `wavepulse-unlimited-20261006-nvme-01` | [会话][L-NVME-LUNA] | 1933、1938、1953、2052；另见封存候选及评分 |
| 扩大检查范围 | `deepseek-flash-medium-20261009-01`，RNG | [会话][L-RNG-FLASH] | 705、706、720、758、765、842 |

[L-RNG-FLASH]: ../../../experiments/native-suite-current-20261006/comparisons/deepseek-flash-medium-20261009-01/virtio-rng/codex/model-home/sessions/2026/10/09/rollout-2026-10-09T18-55-26-01a1204d-b54d-7e42-8070-d83c4017e981.jsonl
[L-RNG-SOL]: ../../../experiments/native-suite-current-20261006/comparisons/gpt6-sol-medium-20261008-01/virtio-rng/codex/model-home/sessions/2026/10/08/rollout-2026-10-08T21-24-05-01a11baf-70fa-7022-9134-7ea8754ea9bd.jsonl
[L-NVME-SCOPE]: ../../../experiments/native-suite-current-20261006/comparisons/gpt6-sol-medium-scope-20261009-05/nvme-pci/codex/model-home/sessions/2026/10/09/rollout-2026-10-09T02-26-38-01a11cc4-7002-7183-901f-870990d785d8.jsonl
[L-NET-LUNA]: ../../../experiments/native-suite-current-20261006/comparisons/wavepulse-unlimited-20261006-net-01/virtio-net/codex/model-home/sessions/2026/10/06/rollout-2026-10-06T20-15-15-01a11123-b418-7e71-a400-6463a97a0d0a.jsonl
[L-NET-SOL]: ../../../experiments/native-suite-current-20261006/comparisons/gpt6-sol-medium-scope-20261009-05/virtio-net/codex/model-home/sessions/2026/10/09/rollout-2026-10-09T02-54-05-01a11cdd-9133-7361-a391-688132671415.jsonl
[L-NVME-SOL]: ../../../experiments/native-suite-current-20261006/comparisons/gpt6-sol-medium-20261009-04/nvme-pci/codex/model-home/sessions/2026/10/09/rollout-2026-10-09T01-43-06-01a11c9c-9777-7fe2-8b96-4a2f6c4da6fd.jsonl
[L-NVME-LUNA]: ../../../experiments/native-suite-current-20261006/comparisons/wavepulse-unlimited-20261006-nvme-01/nvme-pci/codex/model-home/sessions/2026/10/06/rollout-2026-10-06T23-05-48-01a111bf-da00-71e3-8ce7-db566a3c44c2.jsonl
[T-NET-LUNA]: ../../../experiments/native-suite-current-20261006/trials/codex/virtio-net/wavepulse-unlimited-20261006-net-01/PROMPT.md
[T-NET-SOL]: ../../../experiments/native-suite-current-20261006/trials/codex/virtio-net/gpt6-sol-medium-scope-20261009-05/PROMPT.md
[T-NVME-SOL]: ../../../experiments/native-suite-current-20261006/trials/codex/nvme-pci/gpt6-sol-medium-20261009-04/PROMPT.md
[T-NVME-LUNA]: ../../../experiments/native-suite-current-20261006/trials/codex/nvme-pci/wavepulse-unlimited-20261006-nvme-01/PROMPT.md
[T-NVME-SCOPE]: ../../../experiments/native-suite-current-20261006/trials/codex/nvme-pci/gpt6-sol-medium-scope-20261009-05/PROMPT.md
[S-RNG-CORE]: ../../../experiments/native-suite-current-20261006/trials/codex/virtio-rng/gpt6-sol-medium-20261008-01/source/drivers/char/hw_random/core.c
[S-NET-QUEUE]: ../../../experiments/native-suite-current-20261006/trials/codex/virtio-net/wavepulse-unlimited-20261006-net-01/target/kernel/core/comps/virtio/src/queue.rs
[S-NET-LINUX]: ../../../experiments/native-suite-current-20261006/trials/codex/virtio-net/wavepulse-unlimited-20261006-net-01/source/drivers/net/virtio_net.c
[S-NET-QEMU]: ../../../experiments/native-suite-current-20261006/trials/codex/virtio-net/wavepulse-unlimited-20261006-net-01/target/tools/qemu_args.sh
[S-NET-HARNESS]: ../../../experiments/native-suite-current-20261006/trials/codex/virtio-net/gpt6-sol-medium-scope-20261009-05/target/run_public_case.sh
[S-NET-QEMU-SOL]: ../../../experiments/native-suite-current-20261006/trials/codex/virtio-net/gpt6-sol-medium-scope-20261009-05/target/tools/qemu_args.sh
[S-NVME-RECOVERY]: ../../../experiments/native-suite-current-20261006/trials/codex/nvme-pci/gpt6-sol-medium-20261009-04/target/kernel/core/comps/nvme/src/lib.rs
[S-NVME-PRP]: ../../../experiments/native-suite-current-20261006/trials/codex/nvme-pci/wavepulse-unlimited-20261006-nvme-01/target/kernel/core/comps/nvme/src/lib.rs
[E-PRP]: ../../../driver-experiment-env/results/scoring/NVME_PRP_DIAGNOSIS.zh-CN.md
[V-RNG]: ../../../experiments/native-suite-current-20261006/trials/dpf/virtio-rng/deepseek-v4-pro-high-20261009-rng-01/run/work/stage-work/target_platform_study/report.md
[M-BASELINE]: ../../scripts/native_experiment/comparison.py
[M-GOAL]: ../../scripts/native_experiment/goal_client.py
[M-RELATION]: ../../src/driver_port_factory/migration/route_model.py
[M-PACKET]: ../../src/driver_port_factory/migration/route_model.py
[M-NATIVE]: ../../src/driver_port_factory/platform/native_runner.py
[M-RUNTIME]: ../../scripts/native_experiment/runtime.py
[M-KB]: ../../src/driver_port_factory/knowledge/problem_packet.py
[M-KB-ROUTE]: ../../src/driver_port_factory/knowledge/route_learning.py
[M-KB-LEARNING]: ../../src/driver_port_factory/knowledge/learning.py
[M-ANALYSIS]: ../../src/driver_port_factory/data/prompt-packs/default/analysis-task.md
[M-BEHAVIOR]: ../../src/driver_port_factory/data/prompt-packs/default/behavior-job.md
[M-REVISION]: ../../src/driver_port_factory/migration/route.py
