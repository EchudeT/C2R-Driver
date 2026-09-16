# Skill 规范追踪矩阵

本文件把 `C-kernel-to-Rust` 仓库中的三套 Skill 当作 DPF 的规范源。`IMPLEMENTED`
表示已有确定性门禁和测试，`PARTIAL` 表示只有数据结构或阶段骨架，`PLANNED` 表示尚未实现。
任何 `PARTIAL/PLANNED` 项都不能被报告为对应 Skill 阶段已完成。

## 迁移入口：open-kernel-driver-port

| Skill 要求 | 程序阶段/模块 | 强制产物或检查 | 状态 |
|---|---|---|---|
| clone 前确认 source、target、唯一驱动及设备/总线范围 | `request_intake` 至 `migration_envelope_freeze`、`intake/` | request、候选、一次合并问题、确认答案、冻结 envelope | IMPLEMENTED |
| 歧义状态可持久恢复且不得重复提问 | `intake/service.py`、SQLite ledger | `WAITING_FOR_USER` 与 answer 事件 | IMPLEMENTED |
| source/target/QEMU 使用完整提交而非浮动分支 | `revision_selection`、`acquisition/` | revision manifest、解析命令证据 | PARTIAL |
| 上游输入只读、target baseline 与 writable worktree 分离 | `repository_acquisition`、`acquisition/` | 三个 baseline lock、独立 target-working、可恢复 partial clone | PARTIAL |
| 最小证据闭包覆盖源码、测试、目标、QEMU、硬件和工具链 | `evidence_closure`、`acquisition/facets.py` | typed proposal、逐文件 Git blob/external origin、不可变语料 artifact、coverage、gap、retrieval ledger 与 bundle gate | PARTIAL |
| 环境恢复必须实际达到 `EXPERIMENT_READY` | `environment_recovery` | 三仓复验、冻结 QEMU 身份、真实 QMP greeting/capabilities、不可覆盖 run | IMPLEMENTED |
| 不能默认完整源码构建，须比较 runner/SDK/image/injection/CI/full build | environment artifact discovery | 候选路线与选择证据 | PARTIAL |
| 建立或复用带完整性、search、show、rebuild 的项目知识库 Skill | `knowledge_base` | KB status、query contract、生成 Skill | PARTIAL |
| 目标专项检索失败时直接查源码、补语料、重建并复测 | KB target probes | probe 与 repair ledger | PARTIAL |
| 完整 handoff，不能只写“缺少构建信息” | bootstrap handoff | `handoff.json` 全字段验证 | PLANNED |

## 迁移执行：knowledge-guided-driver-port

| Skill 要求 | 程序阶段/模块 | 强制产物或检查 | 状态 |
|---|---|---|---|
| 先完成目标平台画像、API 表和相似驱动端到端链路 | `target_platform_study` | target profile、API evidence、analog trace | IMPLEMENTED |
| 固定真实 C 编译配置并递归关闭共享源码、头、配置、callback、注册表、测试和框架依赖 | `source_closure`、`source_analysis/` | compiler identity、compile database、依赖扫描、七类 closure、KB revision | PARTIAL |
| 导出 AST/CPG/CFG/layout/preprocessor/call/global/effect 和精确 source spans | `structured_c_analysis`、`source_analysis/` | Clang/LLVM 原始 facts、AST-derived CPG、工具/命令/输入摘要；bundle gate 重新绑定 checkout/compiler/argv/bytes/unit，重建 AST semantic index，并重放冻结命令拒绝同步伪造 | IMPLEMENTED |
| 在编码前建立硬件/源/目标/QEMU 四域迁移合同 | `migration_contracts` | 每项证据、Rust 设计、验证 oracle、独立状态 | PARTIAL |
| 驱动逻辑按合同重构而非逐行或按名称猜测 | `rust_design`、`rust_implementation` | source-to-contract coverage 与 unsafe obligations | PARTIAL |
| 修改既有目标文件前证明必要性并选择最低 change level | target-change gate | necessity record、baseline、patch、rollback | PARTIAL |
| 分类每个源测试并尽量保留设备意图，只替换平台 harness | `test_adaptation`、device/platform adapters | 七类 taxonomy、映射、来源、适配和排除理由 | PARTIAL |
| 公共测试与新增迁移测试不得冒充私有/独立测试 | test provenance gate | `SOURCE/ADAPTED/NEW_MIGRATION_TEST` | PARTIAL |
| 产物身份必须证明当前驱动实际进入 QEMU | `artifact_preparation` | base/payload/final hashes、insertion proof | PLANNED |
| 按 evidence ladder 运行并保留每次失败 | `public_qemu_validation`、run store | plan、命令、日志、oracle、状态与归因 | PARTIAL |
| 公开失败按类别窄修复并复跑受影响测试和回归 | `public_repair` | budget、diagnosis、patch scope、reruns | PARTIAL |
| 最终按合同而非单一总分报告 | `completion_audit` | evidence audit | PARTIAL |

## 独立评测：blind-c2rust-driver-evaluation

| Skill 要求 | 程序阶段/模块 | 强制产物或检查 | 状态 |
|---|---|---|---|
| 先冻结 `PROSPECTIVE_BLIND`、`POST_HOC_SEALED_BLIND` 或 `DEVELOPER_EVIDENCE` | role workflow、policy | mode/role compatibility | IMPLEMENTED |
| curator、migrator、evaluator、auditor 工作流和产物边界分离 | role-specific DAG | 不兼容阶段不可出现 | IMPLEMENTED |
| post-hoc 必须先 opaque 接收候选摘要，再设计/承诺私测，再语义导入 | curator/evaluator chronology | 状态链、摘要与 exposure record | PARTIAL |
| PMC 公开、PEA/checker/seed/fault schedule 私有 | bundle policy | 跨域 allowlist 与泄漏扫描 | PLANNED |
| 私有测试必须由未见候选的盲测 AI 冻结并形成可执行断言 | curator Codex jobs | AI identity、exposure、PMC/PEA/harness | PARTIAL |
| commitment 与关键 ledger 事件须外部 WORM/可信时间戳锚定 | anchoring provider | receipt 验证；本地自签不能代替 | PLANNED |
| 候选封存后不可修复或替换第一次尝试 | candidate sealer | canonical bundle、attempt/digest/transfer | PARTIAL |
| G0 至 G9 固定顺序、零反馈执行 | evaluator workflow | 每门结果与最早终止失败 | PARTIAL |
| 普通过程/容器隔离不能冒充独立 VM/物理机边界 | deployment verifier | mounts、credentials、context、VM identity | PLANNED |
| `NON_INDEPENDENT`、`HARNESS_INVALID`、`NOT_RUN` 等精确报告 | result model | gate-by-gate report | PARTIAL |

## 通用性约束

- 核心代码不得包含具体驱动、寄存器、设备 ID、总线前端或测试用例特例。
- 具体驱动身份来自带版本和摘要的 catalog/provider；具体硬件逻辑来自任务证据和插件。
- 扩展轴保持正交：source platform、target platform、device class、QEMU/emulator backend。
- NE2000、UART 或块设备只能作为 fixture/集成样例，不能成为生产路径默认值。
- 每次新增阶段都必须同时补上 Skill 来源、输入/输出 Schema、门禁、失败状态和回归测试。
