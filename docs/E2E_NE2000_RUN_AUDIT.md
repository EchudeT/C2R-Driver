# NE2000 端到端运行审计

本文件记录 `e2e-ne2000-live` 的逐阶段实际运行，而不是设计预期。审计依据包括
`run.sqlite3` 中的阶段/产物账本、CAS 中冻结的 Prompt 和响应、Codex event log、项目本地
知识库以及目标工作树。运行恢复时保留已通过阶段，不重复执行。

## 本轮边界

- 驱动：Linux `ne2k-pci`，PCI NE2000-compatible / QEMU `ne2k_pci`。
- 目标：Asterinas，开发者公开证据模式。
- 恢复点：阶段 10 `knowledge_base`；阶段 1–9 使用已冻结的 PASS 产物。
- 代码基线：`f086a64`。
- 禁止把 source/model-only QEMU 结果报告成迁移驱动运行 PASS。

## 逐阶段观察

| # | 阶段 | 当前观察 | 结论 / 后续优化 |
|---:|---|---|---|
| 1 | project_init | 项目、角色和模式已冻结。 | 静态步骤，无需 Codex。 |
| 2 | request_intake | 三项用户输入齐全。 | 静态步骤，无需优化。 |
| 3 | driver_candidate_resolution | `ne2k-pci` 唯一解析到 PCI/RTL-8029 范围。 | 保持一次性身份门禁。 |
| 4 | scope_confirmation | 总线、设备族与排除范围已确认。 | 不重复询问已冻结范围。 |
| 5 | migration_envelope_freeze | 迁移语言、QEMU 和设备范围已冻结。 | 保持静态。 |
| 6 | revision_selection | 4 份响应；前三次因 citation excerpt 不是远端原文的连续字节子串而退回。 | 已在 Prompt 明确 byte-for-byte contiguous substring；不新增框架。 |
| 7 | repository_acquisition | Linux、Asterinas、QEMU 固定 revision 已获取并哈希。 | 静态 acquisition 符合 Skill。 |
| 8 | evidence_closure | 1 次通过；25 个 facet 覆盖 source/target/QEMU/hardware/test/tooling。 | 输入和闭包充分。 |
| 9 | environment_recovery | 2 份响应、1 次纠错；得到 direct-device-model 的 RISC-V QMP smoke。 | 仅是 model baseline；最终仍需证明当前驱动进入目标 artifact。 |
| 10 | knowledge_base | 本轮恢复后 1 次通过；补入 Taskless、WithDevice、DmaPool/TSC 与目标编码规则等受影响 originals。只读 status/search/show 已正常工作。 | 旧轮 4 次重试主要来自本地 archive/UTF-8/Cargo/probe 基础设施，不应反馈给模型。 |
| 11 | target_platform_study | 1 次通过；模型复用了旧 thread，依据更新后的 KB 重建五件套，结果为 `target_platform_study-b248cb62-ee47-483e-bf28-106d4af62371.result`。 | 门禁满足，但为少量 KNOWLEDGE findings 重读了旧 study、完整 compliance 和临时结构检查；见优化项 O1。 |
| 12 | migration_handoff | 首次静态组装失败，阶段遗留为 `RUNNING`；错误为 `migration handoff does not bind every upstream artifact`。根因是生成端读取全部历史产物，而 bundle 门禁只比较各依赖阶段当前 attempt 的产物。 | 改为只绑定 `current_artifact_refs`，并允许静态阶段从自身失败遗留的 `RUNNING` 状态恢复；不放宽 handoff 内容门禁。 |
| 13 | source_closure | 等待；已有一次通过的 closure 可在知识更新后静态重验。 | 不应再次调用 Codex 生成相同 closure。 |
| 14 | structured_c_analysis | 等待；由固定 clang 后端生成。 | 保持静态，禁止模型猜 C 语义。 |
| 15 | migration_contracts | 等待；旧轮 7 份响应、6 次 locator/证据 lane 纠错。 | 新 Prompt 已要求精确复制 API locator；见优化项 O2。 |
| 16 | test_adaptation | 等待；旧轮 1 次通过。 | 测试分类符合 Skill，无需扩框架。 |
| 17 | driver_implementation | 等待；旧轮 5 份响应，包含 compliance 驱动的修复。 | 继续复用 implementation thread，只写目标工作树。 |
| 18 | target_compliance | 等待；旧轮 4 份响应，真实发现 API 闭包、锁内 I/O/分配、IRQ 路由、重试和无关修改问题。 | findings 必须按 KNOWLEDGE/IMPLEMENTATION 精确回到最早受影响门禁。 |
| 19 | artifact_preparation | 等待。 | 必须证明 artifact 含当前实现，不能只证明编译命令成功。 |
| 20 | public_qemu_validation | 等待。 | 按固定 evidence ladder 运行并保存外部 oracle。 |
| 21 | public_repair | 等待。 | 仅对公开失败证据作一次有归因的最小修复。 |
| 22 | completion_audit | 等待。 | 静态汇总各 contract/test 状态，不生成乐观总 PASS。 |

## 优化记录

### O1：生成最小 repair delta（高优先级）

当前控制器把完整 compliance 响应路径交给知识和 target-study 节点。模型随后重读旧 study、完整
compliance 和大量无关历史。控制器应静态提取：修复目标、finding ID、受影响 API/change ID、
implementation paths、目标 evidence refs 和必须补齐的符号，形成一个小型不可变 repair delta。
原 thread 只接收该 delta 与当前已验收产物，避免再次发现相同上下文。

### O2：程序绑定 target evidence（高优先级）

`migration_contracts` 应让模型选择 `api_id` 和契约语义，控制器再从已验收 API table 填入对应的
`definition_evidence` / `call_site_evidence`。这样可保留模型判断，同时消除伪造 chunk ID、扩大行范围
和 target lane 误配。当前 Prompt 的“精确复制”是短期修复。

### O3：只给复合 Codex 输出增加 JSON Schema（中优先级）

为 target-study 五件套和 migration contracts 绑定现有领域结构的复合 schema，在模型返回边界阻止
数组/对象形状错误。不要给静态阶段或所有内部对象增加新校验层。

### O4：按有效上下文而非无限历史复用 thread（待量化）

同阶段纠错和短修复应复用 thread；当阶段已经通过、随后因远端 finding 被重新打开且历史发生多次
compaction 时，应比较“缓存复用成本”和“当前验收产物 + repair delta 的新 thread 成本”。本轮完成后
用 event log 的 cached/input/output token 和工具调用数决定阈值，不先硬编码轮数。

## 已验证的本轮改进

- KB 查询只以 `O_RDONLY` 打开 `run.sqlite3`，不创建 WAL/SHM sidecar。
- Codex 已成功执行 KB `status` 和多组 `search`，未再出现 `unable to open database file`。
- 新生成 KB Skill 指向本轮更新后的 authoritative manifest。
- E2E 只运行一个控制器和一个当前 Codex job，没有并发重复翻译。
