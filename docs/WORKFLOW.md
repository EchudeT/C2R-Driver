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
| 10 | knowledge_base | 静态执行 | 工具构建 KB、query contract 和项目 KB Skill；语义检查由目标研究工作者完成 |
| 11 | target_platform_study | Codex+Gate | target profile、API evidence、analog trace、target-change plan |
| 12 | migration_handoff | 静态 | 复用已有证据生成交接身份 |
| 13 | source_closure | Codex+静态工具 | 编译输入 → SOURCE_ANALYSIS → 原 worker 消费事实并完成覆盖自检 |
| 14 | migration_contracts | 混合 | 一份合同与测试计划 |
| 15 | driver_implementation | Codex+静态 | 实现、测试适配、合规自检和源码快照 |
| 16 | artifact_preparation | 混合 | 可运行产物、测试入口和身份检查；必要源码调整自检后原地刷新快照 |
| 17 | public_qemu_validation | 混合 | worker 准备 harness → 控制器执行并冻结 receipt → 原 worker 归因与自检 |
| 18 | public_repair | 条件混合 | 默认静态收尾；unsafe 边界或明确请求才独立审查 |
| 19 | completion_audit | 静态 | 汇总证据与未覆盖范围 |

上表为 `DEVELOPER_EVIDENCE`，检查点数量不是模型调用数量。研究、实现和验证使用同一工作者。
仅 blind mode 在 completion audit 前增加 candidate sealing 和 digest export / candidate transfer。
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

不同回退路径不共用“审查三次”预算；同一目标/触发及阶段实质输入的修复次数记录在 ledger，
允许三次回退，第四次 BLOCKED，不会因重启、新报告路径或原因改写归零。报告正文仍作为反馈保留；
编译参数、代码等实质输入改变后使用新的计数。真实 blocker 明确停止。每次真正执行产生不可覆盖 run；恢复后复用同一
未变化的成功 receipt，不制造重复运行。只有公开阶段允许修复；私有反馈后的修复须创建新实验。

公开执行失败留在原工作会话分类，不默认启动审查者或重开实现。源码变化才回实现；
镜像/客体入口问题回包装；同镜像的 harness/oracle 问题留在运行。当前回退原因和冻结报告正文
随任务恢复，修复后只做受影响检查。补报告不触发实现或 QEMU 重跑。Rust 审查按变化语法单元
及跨文件名称依赖定位，不因无关旧 unsafe、注释或格式修改触发。宏/通配导入、语法错误和
分析超限明确保守回退；不是完整类型/动态调用分析。代码、计划、镜像与
harness 输入不变的已通过独立审查可复用。历史证据保留在 CAS 与 ledger，不增加旧执行协议。
