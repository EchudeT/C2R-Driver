# 阶段工作流

## 迁移域

| 顺序 | 阶段 | 执行类型 | 必需输出示例 |
|---|---|---|---|
| 1 | project_init | 静态 | project manifest |
| 2 | request_intake | 静态 | 原始请求与三个必需输入 |
| 3 | driver_candidate_resolution | 混合 | 轻量元数据候选表 |
| 4 | scope_confirmation | 混合/用户门 | 唯一候选或一次合并问题 |
| 5 | migration_envelope_freeze | 静态 | 固定驱动、设备、总线、包含与排除范围 |
| 6 | repository_acquisition | 混合 | 一次选择与获取，固定本地真实 commit； 三个 repository lock、source identity、独立 target worktree |
| 7 | evidence_closure | 混合 | plan、受控 materials、逐 facet coverage、gap register、retrieval ledger |
| 8 | environment_recovery | 混合 | artifact mode、experiment route |
| 9 | knowledge_base | 静态执行 | 工具构建 KB、query contract 和项目 KB Skill；语义检查由目标研究工作者完成 |
| 10 | target_platform_study | Codex+Gate | target profile、API evidence、analog trace、target-change plan |
| 11 | migration_handoff | 静态 | 复用已有证据生成交接身份 |
| 12 | migration_contracts | 混合 | 一份源码分析、迁移合同与测试计划 |
| 13 | analysis_review | 独立检查 AI | 合并审查目标研究、源码分析、契约与测试计划；核心结论附精确原文定位，集中反馈全部问题 |
| 14 | driver_implementation | Codex+静态 | 实现、测试适配、合规自检和源码快照 |
| 15 | artifact_preparation | 混合 | 可运行产物、测试入口和身份检查；必要源码调整自检后原地刷新快照 |
| 16 | public_qemu_validation | 混合 | worker 准备 harness → 控制器执行并冻结 receipt → 原 worker 归因与自检 |
| 17 | public_repair | 独立检查 AI | 直接核对原定功能、代码、测试 oracle 和原始日志；最终报告或完整返修反馈 |

上表为 `DEVELOPER_EVIDENCE`，检查点数量不是模型调用数量。研究、实现和验证使用同一工作者。
第 13 步在设计阶段封存前完成，可返回目标研究或契约阶段窄修复。审查者主动调用只读定位工具，程序不自动匹配报告或裁定引用含义。
开发模式到独立检查结束，无程序语义汇总。工作者与检查者各自保持一个会话；实质缺陷交回原工作者，
修复后原检查者复审。所有已知问题一次反馈，保留正确代码和有效测试；不为风格、可选覆盖返修。
单独的 blind mode 在检查后增加 candidate sealing 和 digest export / candidate transfer；本次不运行。
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

同一目标、触发阶段和实质输入的重复前置回退会被拒绝，历史记录保留在 ledger。
工具或交付协议恢复沿用三次停滞保护；重复 PUBLIC_QEMU 请求另按实质输入计数，
三个不同工作者请求后，第四个无变化请求进入 PAUSED。计数落盘，重启和报告改字不会清零；
同一已落盘请求重放不重复计数。输入改变后允许继续；外部条件改变可通过
`stage recovery-resume --reason` 记录原因并恢复。需要重复实验时，在 harness 中明确实验次数，
或记录外部条件变化，不把同一成功 receipt 的重放当成新的实验。
每次真正执行产生不可覆盖 run；未变化的成功 receipt 可复用。私有反馈后的修复须创建新实验。

公开执行失败留在原工作会话分类，不默认启动审查者或重开实现。源码变化才回实现；
镜像/客体入口问题回包装；同镜像的 harness/oracle 问题留在运行。当前回退原因和冻结报告正文
随任务恢复，修复后只做受影响检查。补报告不触发实现或 QEMU 重跑。

代码、计划、镜像、harness、运行证据及最终工作报告一致，且审查规则摘要一致时，
已通过的独立审查才可复用。摘要覆盖审查阶段目标、共享模板、协议和加载的 Skill 文档；
其他阶段目标修改不触发重审。缺少规则摘要的旧审查不自动复用。
规则修改不重写历史证据，也不会自动重开已经完成的项目；此约束用于下一次进入审查阶段。
