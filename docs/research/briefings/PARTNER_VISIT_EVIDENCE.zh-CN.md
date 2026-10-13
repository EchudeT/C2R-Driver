# 合作方视察汇报：原始证据与论证边界

2026 年 10 月 12 日。配套[汇报正文](PARTNER_VISIT_BRIEFING.zh-CN.md)，供汇报者复核，不作为现场展示主线。

本材料优先依据实际提示、原始会话、候选源码、控制器快照及机器运行记录。旧综述仅帮助定位线索；模型生成的分析同样可能错误。只新增／修订本次汇报材料，旧文档保持原样，没有启动新模型迁移或新 QEMU 实验。

## 1. 阅读与归因口径

主仓库分支为 `experiment/package-execution-focus-20261012`，当前提交 `450a83d443774a7f88ecd0677090691351148af1`。10 月 11 日结果冻结 Factory 为 `ac3f923bd070df2d3bde131aad01b3f883016156`，不能归到当前 HEAD 的最新调整。

v1、v2、v3 是迭代阶段，阶段内部也继续演化；不能根据某一早期 v2 实例宣布整个 v2 没有责任、依赖或托管检查。10 月 Codex 日志是对问题类型的回溯证据，时间晚于 9 月 v1，不能写成它们逐一直接触发了当时的设计。

| 证据类别 | 本文采用方式 |
| --- | --- |
| 中间缺陷且后来修复 | 说明迁移困难、发现时点和修复过程，不归为最终失败。 |
| 静态源码差异 | 确认条件／状态链违反，明确是否有动态复现。 |
| 模型叙述 | 与实际补丁、候选或工具结果交叉核对，不能独立当作执行成功。 |
| 控制器机制 | 依据实际代码；解释结构与进度协调，不宣称证明语义完整性。 |
| 结果与费用 | 对应冻结版本和具体尝试，保留失败、中断和重跑，不择优替换。 |

本次没有使用已移除的 E1000 原工作区来作新的原始日志结论。历史 E1000 审计可以作为后续线索，但未纳入正文的主证据。

<a id="codex"></a>
## 2. Codex：多案例的实际问题与修复

基线已经给出语义保留与真实集成要求。[RNG 基线提示](../../../../driver-experiment-env/docs/experiments/baseline-prompts/virtio-rng.md)要求读取共享调用方、处理正常和失败条件，明确测试不是规格。不能将对照描述成“单纯 C→Rust 语法翻译”。

下列行号均为 JSONL／stdout 日志的**物理行号**。各日志的不同工具输出长度差异很大，行号不是阶段轮数。

<a id="c1"></a>
### C1. RNG：调用方缓存与设备接入

[Sol 原始会话](../../../../experiments/native-suite-current-20261006/comparisons/gpt6-sol-medium-20261008-01/virtio-rng/codex/model-home/sessions/2026/10/08/rollout-2026-10-08T21-24-05-01a11baf-70fa-7022-9134-7ea8754ea9bd.jsonl)：第 556 行涉及接入遗漏；709 行实际工具输出显示缓存预热后的非阻塞读取返回长度不符，该函数有一项断言失败；717 行说明共享缓存与直接用户读取的区别；718 行有实际缓存修补；764 行解释补充数据的时点。

[冻结 Linux core.c](../../../../experiments/archived-source-checkouts/linux-13ec4fe696db/drivers/char/hw_random/core.c)第 245–285 行已独立核对：共享调用方先向驱动取缓存，再交给用户，空缓存时才重新取。责任属于完整调用链，不应仅由设备驱动文件推断。

[Flash 原始会话](../../../../experiments/native-suite-current-20261006/comparisons/deepseek-flash-medium-20261009-01/virtio-rng/codex/model-home/sessions/2026/10/09/rollout-2026-10-09T18-55-26-01a1204d-b54d-7e42-8070-d83c4017e981.jsonl)：652 行设备不存在、检查跳过；678–679 行识别并修复接入；705 行 15 项函数通过。退出码为零不能代替设备运行；但模型识别并修好跳过，不能说它最终误把跳过当成功。

两者为历史中间问题。新版 10 月 11 日 RNG 的公开 14/15 是另一候选，本文没有建立其失败与此缓存问题的根因联系。

<a id="c2"></a>
### C2. VirtIO Network：权限与特性组合

[原始会话](../../../../experiments/native-suite-current-20261006/comparisons/wavepulse-unlimited-20261006-net-01/virtio-net/codex/model-home/sessions/2026/10/06/rollout-2026-10-06T20-15-15-01a11123-b418-7e71-a400-6463a97a0d0a.jsonl)：902–903 行接收权限修复；1045–1046 行发送权限修复；2183–2184 行现代设备报文头由 10 改为 12 字节，含实际修改。

交叉核对[目标 queue.rs](../../../../experiments/native-suite-current-20261006/trials/codex/virtio-net/wavepulse-unlimited-20261006-net-01/target/kernel/core/comps/virtio/src/queue.rs)约 239–315 行，明确设备可读／可写描述符；[Linux virtio_net.c](../../../../experiments/archived-source-checkouts/linux-13ec4fe696db/drivers/net/virtio_net.c)约 6919–6923 行，条件为合并接收特性**或现代版本**。不是只看一种特性就能判断布局。

这些是实现中的实际错误与修复，不证明后续所有网卡失败都由它们引起。

<a id="c3"></a>
### C3. VirtIO Network：错误观察通道影响诊断

[Sol 原始会话](../../../../experiments/native-suite-current-20261006/comparisons/gpt6-sol-medium-scope-20261009-05/virtio-net/codex/model-home/sessions/2026/10/09/rollout-2026-10-09T02-54-05-01a11cdd-9133-7361-a391-688132671415.jsonl)：1469 行开始低速运行调查；1822 行调整初始化；1915 行实际补丁；1923 行因怀疑卡在 `/init` 撤销；2130 行重建早期代码控制；2191 行修改观察器同时读取 stdout 与 `qemu-serial.log`；2227 行确认服务此前已运行、观察位置错误；2254 行 429/429 收发。

1469 与 2227 行时间为 `2026-10-08T19:23:29.258Z` 和 `19:43:56.072Z`，约 20 分 27 秒，包括合理调查与构建，不是可直接节省的浪费时间。目标运行脚本与 QEMU 参数中的串口重定向已核对。正文用它说明运行反馈要有明确观察边界，不作为方法独占的语义创新。

<a id="c4"></a>
### C4. NVMe：恢复覆盖待重试的写缓冲区

[Sol 原始会话](../../../../experiments/native-suite-current-20261006/comparisons/gpt6-sol-medium-20261009-04/nvme-pci/codex/model-home/sessions/2026/10/09/rollout-2026-10-09T01-43-06-01a11c9c-9777-7fe2-8b96-4a2f6c4da6fd.jsonl)：408 行六项公开断言通过；750 行恢复识别借用数据 DMA 缓冲区；757 行失败后恢复再重试；854 行发现写入数据可被覆盖；862 行补丁分离识别缓冲区。

实际修改链支持静态违反：恢复写入识别数据，重试继续使用原待写缓冲区。没有故障注入后观察到磁盘损坏；缺陷随后修复。适合支持“正常与错误路径共享不变量”，不适合宣布最终数据事故。

<a id="c5"></a>
### C5. NE2000：PIO 缓冲区与中断分配上下文

[Sol 原始会话](../../../../experiments/public-suite-info-20261006/comparisons/gpt6-sol-medium-repeat-codex-ne2k-20261011-01/ne2k-pci/codex/model-home/sessions/2026/10/11/rollout-2026-10-11T21-06-15-01a12b12-3236-7f82-b50d-a5fb58cfc0f3.jsonl)：

| 物理行 | 已核对的过程 |
| --- | --- |
| 49、127 | 识别目标 DMA 接口与源 PIO 的区别，读取源发送槽与接收边界。 |
| 512、586、600–601 | 探测通过；586 行实际工具输出含 `AccessDenied`，位置为网络接收缓冲区；600–601 行解释并修改 PIO 存储权限。 |
| 789、847 | 实际流量 96 次交换、16 帧突发通过；恢复通过。 |
| 1036、1140 | 继续补充源发送错误、超时／溢出与可失败分配。 |
| 1258、1263 | 1258 行实际工具输出含 `assertion failed: irq::is_local_enabled()`；1263 行说明空接收池的新策略使首次分配发生于禁用中断时，调整回预先准备。 |
| 1418 | 三项再次通过后，检查到保留帧可能触发关闭中断时的映射，转用 CPU 拥有的存储。 |
| 1544、1630 | 重建，最终三项通过。 |

此例有多次成功与后续自查，不应描述为 Codex 无法处理该驱动。它说明缓冲区语义、生命周期、分配与中断条件相互影响。v3 也遇到相同权限与分配问题，见 V3-B。

<a id="c6"></a>
### C6. evbug：公开样例通过后继续检查源观察范围

[Sol 原始会话](../../../../experiments/public-suite-info-20261006/comparisons/gpt6-sol-medium-20261008-01/evbug/codex/model-home/sessions/2026/10/08/rollout-2026-10-08T21-01-21-01a11b9a-a339-7480-b90a-27aa9620c79e.jsonl)：47 行采用有生命周期的注册 token；588 行公开 3/3 后，发现目标事件只覆盖 SYN／KEY／REL，源 evbug 则观察所有交付事件；642 行加入原始三元组表示；839 行最终 3/3。

模型能自主源行为自查且最终修复。没有证明目标平台未来所有事件生产者都已实现；应限定在本次实际交付与冻结范围，不能把未接入的未来生产者算成 evbug 必须提供的功能。

### C7. VirtIO Block：成功实例，不把环境困难冒充语义失败

[Sol 原始会话](../../../../experiments/native-suite-current-20261006/comparisons/gpt6-sol-medium-20261008-02/virtio-blk/codex/model-home/sessions/2026/10/08/rollout-2026-10-08T22-40-01-01a11bf4-f615-7f20-8c13-0c700a49695b.jsonl)：58 行规划源／目标所有权；95 行保持 BIO 段直到完成，方向正确；138 行 SDK 挂载；442 行启动命令解析；514 行旧容器保留磁盘；615 行 stdin 未接入；650、795 行 3/3 并完成同步。

这例支持 Codex 的有效规划，也支持运行装配存在重复工作，不支持核心行为迁移失败。

<a id="v1"></a>
## 3. v1：证据交付的实际价值与反馈距离

### V1. 2026-09-20 NE2000 原始阶段链

保存原始材料的[归档包](../../../../archived-runs/history-before-audit-07.tar.gz)。本次直接使用 `tarfile` 读取成员，未解压覆盖工作区。所有下列路径相对于归档内 `e2e-ne2000-audit-02/.dpf/codex/`。

| 成员与物理行 | 原始事实 | 论证作用 |
| --- | --- | --- |
| `target_platform_study-53903af5-69eb-4262-bb82-ac0fb71c8ccd.events.jsonl`，109 | 接收缓冲区缺 CPU／PIO 填充接口；索引缺 INTx→GSI 原始路由，请求证据修复。 | 识别资料不足与真实平台缺口。 |
| `target_platform_study-60641813-7ab7-498d-a4ec-1492c7c76014.events.jsonl`，38、57 | KB READY，349 chunks／118 target chunks；仍保留缓冲区、中断路由和锁／PIO 约束，仅完成源与知识研究。 | 知识就绪不等于保证已经成立；v1 已有检索。 |
| `driver_implementation-9e253763-0eeb-4676-8ab3-2839cd3c3a92.events.jsonl`，85 | 混合宽度端口别名违反安全所有权，先采用 16 位传输。 | 广泛研究后仍有路线特定条件。 |
| 同上，503 | 发现 PIO 发生在启用 PCI IO_SPACE 之前，补启用／失败恢复，调整窄范围接口与传输策略。 | 实现反馈会改变方案。 |
| 同上，797、806 | format／check／clippy、六个驱动 ktests 和接收缓冲区测试通过；真实 ne2k_pci 探测、TX/RX、IRQ、重复启动明确 NOT_RUN。 | 辅助检查与完整设备反馈存在距离；未谎称整体成功。 |
| `artifact_preparation-63284f70-4a22-48a8-bdb7-3bf7192c319c.events.jsonl`，94、127、184 | 真设备探测正确；目标拒绝通配地址绑定，按固定地址适配、重建；单次启动 71 次精确交换及 QMP／pcap／控制检查通过，三次启动仍归控制器。 | 必需的目标调用链问题到产物阶段才解决。 |

同名 `.metrics.json` 已核对：研究修补后 400.081 秒；实现 5067.129 秒（84 分 27 秒）；产物阶段 1862.105 秒（31 分 02 秒），模型 `gpt-5.6-sol`。不能全部计作浪费，也不能与不同范围／模型的新运行作加速比。

归档 `project.json` 创建于 `2026-09-20T13:53:34Z`。未保留足以确认启动控制器精确提交的记录。读取的同期 `ce92320` 中 `target_study/service.py` 将**同一份报告**登记为多个证据角色；`validation.py` 有文档结构检查。不能说六个角色必然让模型分别写六份报告，也不能把同期代码假定为此次运行的精确快照。

<a id="v2"></a>
## 4. v2：改进与剩余问题

### V2-A. pvpanic：源条件读到，但交接中没有保留

运行根：[pvpanic-pci-luna-20260930-08](../../../../driver-port-lab/runs/pvpanic-pci-luna-20260930-08)。

- [原始源码读取](../../../../driver-port-lab/runs/pvpanic-pci-luna-20260930-08/commands/model-analysis-ee9f3ea8ff4a/stdout.log)第 6、22 行有 `spin_trylock` 失败返回。
- [实际分析](../../../../driver-port-lab/runs/pvpanic-pci-luna-20260930-08/analysis.md)保留状态锁和串行化，未保留获取失败不等待。
- 实际分析提示 `model/request-563578b7087c.md` 要求简短路线／重要约束，未要求责任关系索引；实施提示 `model/request-5b971d10d0ee.md` 内联该摘要。不是假设出来的交接。
- [候选 driver.rs](../../../../driver-port-lab/runs/pvpanic-pci-luna-20260930-08/target/kernel/core/comps/dpl-pvpanic/src/driver.rs)第 138–139 行使用 `STATE.lock()`。
- 实施 `commands/model-implementation-690bd59d0a08/stdout.log` 第 18–19 行修编译问题；23、26、31 行串行事件／关机检查通过。

确认源／目标静态不一致；本次未对该候选做持锁动态复现。排除动态并发测试不等于授权改变源不等待条件。

### V2-B. NE2000：持续实现完成设备，但重复诊断需要新证据打破

运行根：[ne2k-pci-luna-20261001-06](../../../../driver-port-lab/runs/ne2k-pci-luna-20261001-06)。[持续更新的分析](../../../../driver-port-lab/runs/ne2k-pci-luna-20261001-06/analysis.md)、[事件](../../../../driver-port-lab/runs/ne2k-pci-luna-20261001-06/events.jsonl)、[最终结果](../../../../driver-port-lab/runs/ne2k-pci-luna-20261001-06/result.json)已读。

选定能力为 probe→traffic→recovery，实现会话 `01a0f3f6-aa87-7213-b133-81e05e2f9518` 持续。下面文件均位于该根的 `commands/`：

| 原始 stdout.log | 物理行与观察 |
| --- | --- |
| `model-implementation-659005b7e159/stdout.log` | 51：纠正 IRQ11 与目标向量混用；64：probe 通过。 |
| `model-implementation-ae6f216f0d41/stdout.log` | 32、39、44、53、64、75：路由／发布竞态／DMA 命令／宽度／PROM／RDC 等实际调整；有外部帧，无接收完成。 |
| `model-implementation-23825140a746/stdout.log` | 5：沿用无 IRQ 解释，同时仍有代码修改。 |
| `model-implementation-e0690d1517f5/stdout.log` | 7：同一解释，动作以命令为主。 |
| `model-implementation-7a6462dc810a/stdout.log`、`model-implementation-e826013ee2f8/stdout.log` | 各 3：仍解释为回调未送达，均无已完成工具动作。 |
| [model-implementation-d77d8653aa8d/stdout.log](../../../../driver-port-lab/runs/ne2k-pci-luna-20261001-06/commands/model-implementation-d77d8653aa8d/stdout.log) | 3：显式恢复后调试工具可用；8：CPU 状态证实 IRQ 等待被打断 send 持有的锁；15、24：无锁上半部与共享端口；31：进展恢复，暴露奇数帧长问题；36：traffic 通过。 |
| `model-implementation-08ab2be7a831/stdout.log` | 恢复路径检查与证据复用完成。 |

`events.jsonl` 第 279、297 行登记 traffic／recovery 完成；307 行最终托管公开 PASS。结果 COMPLETE，public-policy 为 `public-ne2k-pci-wire-lifecycle-v2`，语义 NOT_ASSESSED，并保留 continuation／resume 历史。

这是真实连续实现的成功，不应只列失败。调试工具在显式恢复后可用，不能忽略介入或作纯自动首次运行结论。重复诊断发生过，但不能将所有中间修复归为无效。

### V2-C. 末端语义审查没有稳定补回遗漏条件

[第一次 review.md](../../../../driver-port-lab/runs/pvpanic-semantic-review-diagnostic-20261002-01/review.md)：读源与目标，判断锁串行化 SUPPORTED，遗漏 trylock／等待差异。

[调整后的 review.md](../../../../driver-port-lab/runs/pvpanic-semantic-review-diagnostic-20261002-02/review.md)明确提出 VIOLATION。[离线重解析](../../../../driver-port-lab/runs/pvpanic-semantic-review-diagnostic-20261002-02/reparsed-result.json)保留 FAIL、`delivery_approved:false`；首次解析因 UNVERIFIED 省略空引用字段失败，是辅助格式问题。主证据是前一次漏判、后一次识别，不是格式成为核心研究动机。

### V2-D. 同一实施中正文修订，完成仍绑定旧依据

运行根：[pvpanic-semantic-luna-20261002-04](../../../../driver-port-lab/runs/pvpanic-semantic-luna-20261002-04)。

`events.jsonl`：34 行初始计划；54 行 worker done；56–57 行责任正文更新；58 行 checkpoint `eabe350b…`；59 行完成旧 key `e48a16da…`；60 更新计划；61 同一 events 目标以新 key `be2c9447…` 再次选中；66 再请求检查；73–77 是另一个 TLS 下载失败；75 行未改代码，checkpoint 相同。

本次读取该运行 `controller-source.tar.gz` 的 `src/driver_port_lab/runner.py`：628 行加载 updated 计划，653 行写旧 `unit.key` 完成，682 行采用 updated。是运行精确快照支持的协调问题。责任正文实际改变，不能误称纯引用元数据变化；TLS 环境故障也不能归给进度协调。

<a id="v3"></a>
## 5. v3：责任进入实现、反证进入修订

### V3-A. pvpanic：分析→实现→非等待条件检查

运行根：[pvpanic-route-sol-medium-20261003-09/run](../../../../experiments/pvpanic-route-sol-medium-20261003-09/run)。

[实际分析](../../../../experiments/pvpanic-route-sol-medium-20261003-09/run/work/stage-work/target_platform_study/report.md)：C2 明确获取锁失败立即返回；B1 拥有完整通知、原生钩子、资源与清理；P2 核对目标终止 handler 为首次注册生效，通知插入原生终止之前。[route.json](../../../../experiments/pvpanic-route-sol-medium-20261003-09/run/work/stage-work/target_platform_study/report.route.json)可核对 C2→B1 与 P2→R1。

目标 `ostd/src/power.rs` 52–83 行的 `Once<fn(ExitCode)>` 已独立读取：后来注册不会替换首次处理器。

原始 `.dpf/codex/driver_implementation-41c0f125-f06e-4534-82aa-2fbf126ed382.events.jsonl`：3 行从 B1 开始；34 行非等待实现；40 行首次构建前发现总线保留资源、事件 mask 锁仍可阻塞，分离资源状态并改原子 mask；51 行修具体 API 编译问题；60 行生命周期工具 PASS；62 行原生 panic／shutdown 工具 PASS。

[实际候选](../../../../experiments/pvpanic-route-sol-medium-20261003-09/run/work/target/kernel/core/comps/pvpanic/src/lib.rs)约 136–145 行 `try_lock` 失败返回，包含持锁抑制测试。后续 `.dpf/codex/driver_implementation-299fbfa1-bc30-4307-aa4f-3d1bb64a6a67.events.jsonl` 第 77 行有真实命令 stdout：运行五项测试，`5 passed; 0 failed`，不只模型声称通过。

历史第 64 行开发完整检查复用过之前单项 PASS。此实例支持针对条件的动态观察，**不能写成已验证当前最新“最终完整批次不拼接历史单项”机制**。v2 与此轮模型／功能范围不同，不能作公平总体质量或成本对照。

### V3-B. NE2000：前置 IRQ 条件，但仍有错误前提，经运行反证修订

运行根：[gpt6-sol-medium-premise-ne2k-20261011-01/run](../../../../experiments/public-suite-info-20261006/trials/dpf/ne2k-pci/gpt6-sol-medium-premise-ne2k-20261011-01/run)。

[初始 report.md](../../../../experiments/public-suite-info-20261006/trials/dpf/ne2k-pci/gpt6-sol-medium-premise-ne2k-20261011-01/run/work/stage-work/target_platform_study/report.md)：B1 全驱动；P2 明确软中断禁用不屏蔽硬中断，另设核心状态锁；P3 真实固件中断路由；P4 **错误声称**已有 FromDevice CPU writer 可用。原文件不会因路线修订自动变成新结论。

原始[实施 events.jsonl](../../../../experiments/public-suite-info-20261006/trials/dpf/ne2k-pci/gpt6-sol-medium-premise-ne2k-20261011-01/run/.dpf/codex/driver_implementation-2037e11e-dd0f-4752-8c13-2fa26e2b415f.events.jsonl)：

| 物理行 | 实际过程 |
| --- | --- |
| 3、30 | B1 开始；首次构建前说明中断锁与外层网络锁分离。 |
| 71、96 | 核心状态／集成形成，构建与探测运行。 |
| 105 | 实际流量暴露 IRQ-disabled 分配，移到核心锁之前。 |
| 128 | 实际流量暴露 CPU 写权限拒绝，补可写的 CPU 存储变体。 |
| 141 | traffic／restart 通过，准备修正前提。 |
| 160–163 | 修订路径及索引字段两次错误，属于次要工程重试。 |
| 166–167 | 成功修订，返回 `REVISED_NOT_ACCEPTED` 与修订 digest。 |
| 180、183 | 再构建／流量检查，交回最终完整验收并完成当前目标。 |

[控制器保存的修订对象](../../../../experiments/public-suite-info-20261006/trials/dpf/ne2k-pci/gpt6-sol-medium-premise-ne2k-20261011-01/run/.dpf/cas/objects/sha256/97/d46df93f6c3d811b3e4877e89cffe2972119b9494a4b34229ba01494faabe7)包含 binding 与 text。P4 明确：原 FromDevice CPU writer 返回 AccessDenied；新增 FromAndToDevice 存储变体，原 DMA 路径保留；分配在关闭本地 IRQ 的核心锁之外；T3 反证、T4 traffic 通过；不宣称 PIO 等同真实设备 DMA。

只读 `.dpf/run.sqlite3` 的机器事件：sequence 91 为该 `run.route_revision`；92 将 active／job.selected 更新为 `f8cf414b…`；98 在同一个 job `2037e11e…` 登记该新 key 完成；没有为了确认前提修正而重新开实施轮。

[最终 attempt.json](../../../../experiments/public-suite-info-20261006/trials/dpf/ne2k-pci/gpt6-sol-medium-premise-ne2k-20261011-01/run/.dpf/public-qemu/4803ed871ac84afeb7e3b4048dedcacf/attempt.json)：execution PASS，固定源码文件与产物 `887c5ea0…`；数据库 sequence 110–112 分别采集最终三项日志，115 公开阶段 PASS。追加 A/B 也 3/3，见结果表。

[最终 buffer.rs](../../../../experiments/public-suite-info-20261006/trials/dpf/ne2k-pci/gpt6-sol-medium-premise-ne2k-20261011-01/run/work/target/kernel/core/comps/network/src/buffer.rs)可核对原 DMA 变体和新 CPU 存储入口。

可确认：硬 IRQ 前提在实现前被识别；分配与权限错误仍实际发生；运行反证同时进入实现与后续分析；同目标修订后完成有机器记录。不能确认：v3 普遍预防这些错误、不会死锁或每项收益由特定机制因果产生。

### V3-C. 真实工作包：多个责任可以归一个完整能力

| 驱动与原始分析 | 实际结构 |
| --- | --- |
| [RNG report.md](../../../../experiments/native-suite-current-20261006/trials/dpf/virtio-rng/deepseek-v4-pro-high-20261009-rng-01/run/work/stage-work/target_platform_study/report.md)、[索引](../../../../experiments/native-suite-current-20261006/trials/dpf/virtio-rng/deepseek-v4-pro-high-20261009-rng-01/run/work/stage-work/target_platform_study/report.route.json) | 一个 B，七 C，两 P；R1／P1 识别模块、调用方声明与初始化缺口；C4／C5 区分调用方与驱动读取责任。 |
| [Block report.md](../../../../experiments/native-suite-current-20261006/trials/dpf/virtio-blk/deepseek-v4-pro-high-20261009-remaining-01/run/work/stage-work/target_platform_study/report.md) | 一个 B，六 C，四 P。 |
| [Network report.md](../../../../experiments/native-suite-current-20261006/trials/dpf/virtio-net/deepseek-v4-pro-high-20261009-remaining-01/run/work/stage-work/target_platform_study/report.md) | 一个 B，五 C，两 P。 |
| NE2000，V3-B | 一个 B，六 C，四 P；发送、接收、中断、恢复共同维护状态。 |
| [evbug report.md](../../../../experiments/public-suite-info-20261006/trials/dpf/evbug/gpt6-sol-medium-premise-five-pair-20261011-01/run/work/stage-work/target_platform_study/report.md) | B1 完整观察者生命周期；C2 不按事件白名单过滤；P1 回调清理；P2 保留目标表示边界，不将未来生产者纳入 evbug 责任。 |

上述计划事实不代表所有 C 都动态验证。RNG P2 的某个缓冲区大小下限依赖特定测试／方案，不能据此定为唯一正确要求；C7 生命周期强结论也含未运行推理。正文只使用可核对的责任组织，不继承这些模型结论。

小驱动的单包实例不能证明多包划分／局部失效有性能收益；v2 NE2000 已有多能力目标，不应把“第一次有工作包”当成 v3 创新。

## 6. 当前机制入口与已做离线核对

| 机制 | 当前实际入口 | 检查边界 |
| --- | --- | --- |
| 源行为、目标责任、关键前提联合分析 | [analysis-task.md](../../../src/driver_port_factory/data/prompt-packs/default/analysis-task.md) | 非等待、失败与生命周期条件直接要求；无需全 API 调查，引用不等于正确。 |
| 连续实现，重复失败寻求区分性观察 | [behavior-job.md](../../../src/driver_port_factory/data/prompt-packs/default/behavior-job.md) | 模型指导，不是控制器保证每次取得新信息。 |
| 原文进入工作包，已声明 C 都须归 B | [route_model.py](../../../src/driver_port_factory/migration/route_model.py)、[behavior_prompt.py](../../../src/driver_port_factory/codex/behavior_prompt.py) | 无二次总结计划；结构关系可检查，无法发现未识别的源义务。 |
| 显式依赖、循环合并、进度包含关联依据 | [behavior.py](../../../src/driver_port_factory/migration/behavior.py) | 完成 key 包含关联正文和递归前置 key；遗漏依赖不会被自动补齐。 |
| 同目标修订协调、禁止削弱冻结要求 | [route.py](../../../src/driver_port_factory/migration/route.py)约 165–210 行 | 当前目标与依赖保持时重绑，替换目标不能旧完成充数；不是自动代码回滚。 |
| 具体问题按需查原始源／目标／测试 | [problem_packet.py](../../../src/driver_port_factory/knowledge/problem_packet.py) | 读取控制器记录的修订；相同来源不能证明模型重新读过。 |
| 经验带条件，公开通过后才发布 | [route_learning.py](../../../src/driver_port_factory/knowledge/route_learning.py)、[learning.py](../../../src/driver_port_factory/knowledge/learning.py) | 可选经验，不是自动抽出全部模型发现；本轮外部共享 KB 关闭。 |
| 运行观察边界 | [native_runner.py](../../../src/driver_port_factory/platform/native_runner.py)、[runtime.py](../../../scripts/native_experiment/runtime.py) | ttyS0／serial stdio 与 ready 等待；没有独立消融证明其收益。 |
| 最终完整批次对应同一候选 | [public_qemu.py](../../../src/driver_port_factory/migration/public_qemu.py) | 不拼历史开发单项；已通过的同批次恢复可复用。不能回溯假定每轮历史运行都有最新策略。 |
| 默认审查关闭 | [port.py](../../../src/driver_port_factory/port.py) | 两审查配置默认 false；旧 CLI help／注释写 enabled 已过期。 |

本次沿用现有测试核对了以下五项，`.venv/bin/python -m pytest` 返回五项通过，无新测试、无付费模型调用：

- `tests/test_route_guidance.py::test_source_only_route_needs_no_probe_and_evidence_addition_keeps_progress`
- `tests/test_route_guidance.py::test_route_change_invalidates_only_referencing_behavior_and_dependents`
- `tests/test_route_guidance.py::test_route_correction_inside_same_behavior_does_not_require_an_extra_round`
- `tests/test_route_guidance.py::test_revision_cannot_weaken_contract_or_complete_a_replaced_objective`
- `tests/test_behavior_scheduling.py::test_cycles_merge_and_edited_prerequisite_invalidates_dependent_progress`

支持引用增加不重开进度、改动按声明关系影响下游、同目标修订协调、冻结要求与目标替换限制、循环合并。它们不证明真实驱动语义，也不能量化对照收益。

<a id="results"></a>
## 7. 新版结果及全部尝试口径

数据入口：[新版汇总 Markdown](../../../../driver-experiment-env/results/gpt6-sol-medium-premise-six-comparison-20261011.zh-CN.md)、[对应 JSON](../../../../driver-experiment-env/results/gpt6-sol-medium-premise-six-comparison-20261011.zh-CN.json)。JSON 每项记录列出原始 result/run 路径、测量值、逐请求费用、事故记录和 A/B 评分。本轮已打开已完成记录的原始 result，交叉核对公开通过数。批次名称中的日期与当地日期可能不同，应以实际时间为准。

以下保留各次记录，时间单位为分钟，费用为冻结官方标价估算美元，非中转实付账单。

| 驱动／尝试 | 方法 | 时间 | 费用 | 公开 | A／B |
| --- | --- | ---: | ---: | --- | --- |
| evbug 首轮 | Factory | 12.87 | 1.3917231 | 3/3 | 3/3；3/3 |
| evbug 首轮 | Codex | 14.59 | 1.3017229 | 3/3 | 3/3；3/3 |
| RNG 首轮 | Factory | 12.59 | 2.0521164 | 1/1，15/15 函数 | 1/1；1/1 |
| RNG 首轮 | Codex | 19.96 | 5.1733045 | 0/1，14/15 函数 | 1/1；1/1 |
| Block 首轮资料获取失败 | Factory | 4.57 | 0.4289803 | 空心基线 1/3，非生成质量 | 空心基线 0/3；0/3 |
| Block 首轮 | Codex | 17.96 | 2.5163537 | 3/3 | 3/3；3/3 |
| Network 首轮 | Factory | 29.52 | 6.2547630 | 3/3 | 2/3；3/3 |
| Network 首轮 | Codex | 49.26 | 5.6204423 | 3/3 | 3/3；3/3 |
| NVMe 重新启动 | Factory | 32.46 | 5.5753439 | 1/1 | 1/1；1/1 |
| NVMe 重新启动 | Codex | 29.43 | 4.1435165 | 1/1 | 1/1；1/1 |
| NE2000 单列参考组 | Factory | 21.53 | 3.2609262 | 3/3 | 3/3；3/3 |
| NE2000 单列参考组 | Codex | 26.13 | 8.7154345 | 3/3 | 3/3；3/3 |
| Block 用户要求重跑 | Factory | 30.02 | 3.5819675 | 3/3 | 3/3；3/3 |
| Network 用户要求重跑 | Factory | 47.08 | 6.1652999 | 3/3 | 3/3；3/3 |
| NVMe 原轮操作中断 | Factory | 6.05 | ≥0.8683265 | 未完成 | 未完成 |
| NVMe 原轮操作中断 | Codex | 6.05 | ≥0.7496421 | 未完成 | 未完成 |

Block 与 Network 的 Factory 重跑各含约 7.96 分钟调度暂停，表中没有扣除。重跑费用不能取代首轮费用，操作中断费用为已记录下限；因此不能从完成记录表计算全部尝试总成本或首轮成功率。

Block 分类依据：[资料获取失败收据](../../../../driver-experiment-env/results/batches/gpt6-sol-medium-premise-five-pair-20261011-01/virtio-blk-dpf-acquisition-failure.json)。下载读取中断发生在实现之前，目标工作区尚未修改；公开运行测到的是空心目标，不能把它当成生成驱动的缺陷。

Network 首轮 A 组依据：[机器评分明细](../../../../driver-experiment-env/results/batches/gpt6-sol-medium-premise-five-pair-20261011-01/private/virtio-net/dpf/summary.json)。UDP 接收观察为零，其他场景通过；本轮没有完成根因诊断。不能把该失败直接归因于正文开场的历史协议缺陷。

协议抽样核对：[新版 RNG protocol.json](../../../../experiments/native-suite-current-20261006/comparisons/gpt6-sol-medium-premise-five-pair-20261011-01/virtio-rng/protocol.json)记录模型、版本、设备配置、源目标固定范围与 `cold-external-kb`。[基线提示](../../../../driver-experiment-env/docs/experiments/baseline-prompts/virtio-rng.md)明确要求读取共享调用方、保留正常及失败条件，并声明测试不是规格；不要把 Codex 描述为简单逐函数翻译。

追加评价依据：[参数变种评分说明](../../../../driver-experiment-env/docs/experiments/PARAMETER_VARIANT_SCORING.zh-CN.md)。性质为 `DEVELOPER_EVIDENCE`，不是独立盲测；候选封存后评分不回传修复。同功能参数变种不构成穷尽覆盖，程序／场景与函数计数也不可混用。RNG 的公开 14/15 与变种通过并不矛盾，代表不同输入下的观察，不应改写任一成绩。


旧汇总的处理：`gpt6-sol-medium-comparison.zh-CN.md` 中的 Network 22.20／56.32 分钟、NVMe 16.46／25.49 分钟，与 `batches/gpt6-sol-medium-20261008-02/measurements.json` 的 Network 32.46／20.61、NVMe 79.38／34.98 不对应同一记录。旧汇总涉及后续完成记录，不能当成一个固定批次的统一优势。本次正文移除其“六项更便宜、五项更快”总体论据，采用原始来源可追踪的新结果；旧文件未修改。

<a id="limits"></a>
## 8. 反例、局限与应避免的结论

### Q1. 早期 NVMe 两页地址分支遗漏

[NVMe PRP 诊断](../../../../driver-experiment-env/results/scoring/NVME_PRP_DIAGNOSIS.zh-CN.md)核对早期 Luna 两种候选、Linux 与 QEMU：公开 64 KiB／追加 32 KiB 通过，8 KiB 比较失败；两份候选缺少两页直接地址分支。是源码差异与运行反例，不是平台天然不兼容，尚无修复后因果复验，不能据此解释新版候选。

### 汇报论证的边界

- 多案例支持责任跨边界、目标保证有条件及诊断需要区分性证据；不是测得这些问题的总体发生率。
- v1、v2 有实质改进；v3 同样有误判与缺陷，不应写成前三者失败、v3 消除一切问题。
- Codex 多次自行修复并最终通过；中间问题不能替换最终成绩。
- 引用校验、结构完整与公开通过不证明完整语义等价；当前默认无独立模型审查。
- 历史条件修复例与新版结果不是同一次运行，未诊断的新版失败不能借历史根因解释。
- 六驱动结果呈混合观察，未证明 Factory 稳定优于 Codex 或某组件单独有效。
- 真实 v3 单包案例主要支持责任完整性，不能证明多包拆分和局部重做的性能收益。
- 追加 A／B 是开发证据，不是独立盲测；均为 QEMU，没有实机结论。
- 当前 HEAD 的最新包检查与连续交接调整尚无本次可用的完整设备对照结果。

面向 FSE 的证据缺口为重复配对、组件消融、人工责任／依赖抽样、独立留出评价，以及更大驱动的多包案例。没有用文档质量或单次费用优势替代这些实验。

## 9. 补充汇报第二节的重新核查记录

本节用于内部复核。现场补充材料以 v3 的三个特点组织第二节：前置消化返工、语义对齐、单工作者以精简上下文连续完成完整机制。问题依据沿用主汇报第 2、4 节及下表核查结果；不将核查记录作为汇报内容。

| 重新核查的说法 | 原始依据 | 本次处理 |
| --- | --- | --- |
| 完整调用链的责任较晚落实 | C1 的设备入口遗漏、实际缓存断言失败和调用方补丁；C6 的样例通过后事件表示自查 | 保留为第一类问题。描述发现时点及实际修改，不反推模型没有阅读调用方或只针对测试实现。 |
| 目标保证条件与跨路径状态需要共同维护 | C2 的 RX／TX 权限及报文头补丁；C5 的权限拒绝与中断分配断言；C4 的恢复识别、重试及缓冲区分离补丁 | 合并为第二类问题。NVMe 属于静态错误链，未观察到磁盘损坏；权限、上下文与恢复是同一类责任问题的不同表现。 |
| 运行反馈需要区分原因 | C3 的实际观察器补丁与服务已运行的确认；V2-B 的 CPU 状态定位中断内等待 | 保留为第三类配套问题。错误日志通道不单独提升为核心语义动机；历史 v2 问题仍明确归属 v2。 |
| 检查通过后继续修改说明 Codex 没有确认最终候选 | C6 第 642、839 行明确重建并重测最终 evbug；C5 第 1630 行明确同一最终产物的三项顺序运行 | 删除这一问题归因。修改后继续核查属于成功实践；v3 的最终完整批次作为正向交付机制单独说明。 |

v3 的支持分层：RNG／VirtIO Network／evbug 的实际分析支持责任和前提在规划中明确；NE2000 支持运行事实进入实现、前提修订及同目标完成绑定；pvpanic 支持源条件进入实现和针对性执行；igb 支持真实两包递进和最终六项完整验收。NVMe 两包及正常公开检查不等于已完成恢复故障注入，因此补充汇报只用它说明所有权与依赖的组织。

本次重新打开上述 Codex 原始 JSONL、相关 v3 分析与现行分析／实施提示、调度／修订／公开验收代码。RNG 709 行与 NE2000 586、1258 行的实际失败输出已补入对应证据条目。机制作用按设计解释，案例效果按该运行记录陈述；不从单例推导普遍预防或统计上的因果优势。
