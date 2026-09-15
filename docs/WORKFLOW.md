# 阶段工作流

## 迁移域

| 顺序 | 阶段 | 执行类型 | 必需输出示例 |
|---|---|---|---|
| 1 | project_init | 静态 | project manifest |
| 2 | driver_identity | 混合 | identity record |
| 3 | revision_selection | 混合 | pinned revisions |
| 4 | evidence_acquisition | 混合 | acquisition manifest |
| 5 | environment_recovery | 混合 | artifact mode、experiment route |
| 6 | knowledge_base | 静态 | KB integrity、query contract |
| 7 | target_platform_study | Codex+Gate | target profile、API evidence |
| 8 | source_closure | 混合 | behaviorally required closure |
| 9 | structured_c_analysis | 静态 | AST/CFG/layout/effect facts |
| 10 | migration_contracts | 混合 | evidence-backed contracts |
| 11 | test_adaptation | 混合 | retained/adapted/excluded matrix |
| 12 | rust_design | Codex+Gate | ownership/concurrency/unsafe design |
| 13 | rust_implementation | Codex+静态 | source/patch and contract mapping |
| 14 | target_compliance | 混合 | target rules review |
| 15 | artifact_preparation | 静态 | runtime artifact + identity proof |
| 16 | public_qemu_validation | 静态 | public functional/failure runs |
| 17 | public_repair | 混合 | diagnoses、patches、reruns |
| 18 | completion_audit | 静态 | final coverage and evidence audit |
| 19 | candidate_sealing | 静态 | canonical candidate manifest/digest |

`MIGRATION_OPERATOR` 在前瞻盲测中必须先导入 `public_bundle` 和 `curator_commitment`。事后封存模式在第 19 阶段后只导出 opaque digest；迁移域不负责创建私有测试。

## 策展域

- `PROSPECTIVE_BLIND`：身份/版本/公开证据 → PMC/PEA/阈值/生成器冻结 → commitment → 只导出公开任务包。
- `POST_HOC_SEALED_BLIND`：先接受候选物 opaque digest 并外部锚定 → 在不读取候选内容的前提下冻结 PMC/PEA → 导出私有 bundle 给评测域。

## 评测域

固定顺序为：隔离门、可复现构建、强制合同、外部功能、C/Rust 差分、故障注入、mutation adequacy、压力、性能和预选真机子集。任何一次构建失败、超时、Harness 无效和能力不支持都单独记录，不能删除失败任务。

## 公开修复循环

```text
observation -> classification -> evidence query -> hypothesis
            -> narrow patch -> affected checks -> regression
```

每轮拥有固定预算并产生新的不可覆盖 run。只有公开阶段允许修复。私有结果反馈后的修复必须创建 `POST_FEEDBACK` 实验，不能覆盖 first-attempt 结果。
