# 阶段工作流

## 迁移域

| 顺序 | 阶段 | 执行类型 | 必需输出示例 |
|---|---|---|---|
| 1 | project_init | 静态 | project manifest |
| 2 | request_intake | 静态 | 原始请求与三个必需输入 |
| 3 | driver_candidate_resolution | 混合 | 轻量元数据候选表 |
| 4 | scope_confirmation | 混合/用户门 | 唯一候选或一次合并问题 |
| 5 | migration_envelope_freeze | 静态 | 固定驱动、设备、总线、包含与排除范围 |
| 6 | revision_selection | 混合 | pinned revisions |
| 7 | repository_acquisition | 静态 | 三个 repository lock、source identity、独立 target worktree |
| 8 | evidence_closure | 混合 | plan、受控 materials、逐 facet coverage、gap register、retrieval ledger |
| 9 | environment_recovery | 混合 | artifact mode、experiment route |
| 10 | knowledge_base | 静态 | KB integrity、query contract、target probes、生成的项目 KB Skill |
| 11 | target_platform_study | Codex+Gate | target profile、API evidence、analog trace、target-change plan |
| 12 | source_closure | Codex+静态 Gate | frozen compile manifest、compiler-discovered closure、KB revision |
| 13 | structured_c_analysis | 静态 | Clang AST/CFG/layout/preprocessor、LLVM IR、CPG/call/global/effect/span indexes |
| 14 | migration_contracts | 混合 | evidence-backed contracts |
| 15 | test_adaptation | 混合 | retained/adapted/excluded matrix |
| 16 | rust_design | Codex+Gate | ownership/concurrency/unsafe design |
| 17 | rust_implementation | Codex+静态 | source/patch and contract mapping |
| 18 | target_compliance | 混合 | target rules review |
| 19 | artifact_preparation | 静态 | runtime artifact + identity proof |
| 20 | public_qemu_validation | 静态 | public functional/failure runs |
| 21 | public_repair | 混合 | diagnoses、patches、reruns |
| 22 | candidate_sealing（仅 blind mode） | 静态 | canonical candidate manifest/digest |
| 23 | digest export / candidate transfer（仅 blind mode） | 静态 | anchored digest or transfer record |
| 24 | completion_audit | 静态 | final read-only coverage, chronology and evidence audit |

`DEVELOPER_EVIDENCE` 跳过第 21–22 阶段，在 public repair 后直接执行 completion audit。
`MIGRATION_OPERATOR` 在前瞻盲测中必须先导入 `public_bundle` 和 `curator_commitment`。
事后封存模式在候选封存后只导出 opaque digest；迁移域不负责创建私有测试。
`request_intake` 至 `migration_envelope_freeze` 全部通过前，acquisition 不得 clone 内核、镜像或工具链。

## 策展域

- `PROSPECTIVE_BLIND`：身份/版本/公开证据 → PMC/PEA/阈值/生成器冻结 → commitment → 只导出公开任务包。
- `POST_HOC_SEALED_BLIND`：先接受候选物 opaque digest 并外部锚定 → 在不读取候选内容的前提下冻结 PMC/PEA → 导出私有 bundle 给评测域。

## 评测域

固定顺序为：隔离门、可复现构建、强制合同、外部功能、C/Rust 差分、故障注入、mutation adequacy、压力、性能和预选真机子集。任何一次构建失败、超时、Harness 无效和能力不支持都单独记录，不能删除失败任务。

所有 `INDEPENDENT` 阶段必须从未参与迁移、未保留候选语义的新 Codex 上下文/进程运行，并使用
角色专属工作区和凭据。`codex exec --ephemeral` 或 SDK 新 thread 防止会话恢复，但共享文件、工具
日志、私有凭据或迁移上下文本身仍会使结果成为 `NON_INDEPENDENT`。

## 公开修复循环

```text
observation -> classification -> evidence query -> hypothesis
            -> narrow patch -> affected checks -> regression
```

每轮拥有固定预算并产生新的不可覆盖 run。只有公开阶段允许修复。私有结果反馈后的修复必须创建 `POST_FEEDBACK` 实验，不能覆盖 first-attempt 结果。
