# 架构设计

## 1. 架构约束

DPF 是证据驱动的工作流控制器，不是拥有全局权限的长对话。其不可弱化的约束是：

1. 阶段、必需输出和角色顺序直接映射三套上游 Skill；代码整洁不能绕过证据门禁；
2. 程序拥有状态、哈希、运行和封存，Codex 只提交当前阶段允许的有类型产物；
3. 产物只有一条成功登记路径，缺失、未知或不合法的产物必须 fail closed；
4. 硬件协议、来源平台机制、目标平台机制和 QEMU 行为是不同证据域；
5. 迁移公开修复、策展私有材料、独立评测和只读审计使用不同角色工作流；
6. 生产代码不得内置 NE2000、UART、块设备或任何具体驱动特例。

阶段存在不等于阶段已经实现完整。当前门禁覆盖度及 `IMPLEMENTED/PARTIAL/PLANNED`
状态以 [SKILL_TRACEABILITY.md](SKILL_TRACEABILITY.md) 为准。

## 2. Bounded contexts

每个领域拥有自己的阶段枚举、产物枚举、封闭状态、服务和 validator。不存在包含全部阶段或
全部产物的全局枚举，也不存在按产物名称分派的巨型 `if/elif`。

| Context | 责任 | 自治契约示例 |
|---|---|---|
| `control` | 项目创建、通用控制命令、ledger 验证 | `ControlStage`、`ControlArtifact` |
| `intake` | clone 前需求解析、候选消歧、范围冻结 | `IntakeStage`、`IntakeArtifact`、`IntakeStatus` |
| `acquisition` | revision、受控 repository、逐 facet 证据闭包 | `AcquisitionStage`、各域 facet、typed origin/gap |
| `environment` | artifact mode 与可执行实验路线 | `EnvironmentInspector`、`ExperimentPlanRegistrar`、`ExperimentExecutor` |
| `knowledge` | 消费不可变受控语料、建立索引、生成项目 KB Skill；语义探查归目标研究 | `CorpusManifest`、`KnowledgeIndex`、`KnowledgeBootstrapper` |
| `target_study` | 目标画像、API 原文证据、相似驱动链路 | target-study contracts 与 validators |
| `migration` | 合同、测试适配、Rust 实现、运行和审计产物 | `MigrationStage`、`MigrationArtifact` |
| `sealing` | 候选 manifest、摘要锚定和 transfer | `SealingStage`、`SealingArtifact` |
| `evaluation` | curator/evaluator/auditor 的盲测和审计产物 | `EvaluationStage`、`EvaluationArtifact` |
| `codex` | Prompt 产物、最小权限策略及 SDK/exec 边界 | backend、sandbox、event 类型 |

领域内部使用枚举或 value object。JSON 字段名、CLI option、SQL 列名和外部工具 argv
仍然是边界协议文本，不会被机械塞入一个“全局常量仓库”。

## 3. 依赖方向与 composition root

`core` 只依赖 `ArtifactKey` 和 `StageKey` structural protocol，不导入任何领域枚举。它只知道：

- 一个 key 有稳定的 canonical `value`；
- `StageSpec` 声明依赖、角色和必需输出；
- validator 能验证单产物或阶段 bundle；
- 状态机按 `StageStatus` 原子迁移。

`composition.py` 是唯一 composition root。它组合各领域 validator group，选择角色专属工作流，
并在创建 `WorkflowDefinition` 时验证每个 required output 都有 validator。重复 validator、未知阶段、
未知依赖和缺少 validator 都在运行前失败。`orchestration/migration.py` 与
`orchestration/blind.py` 只组合领域契约，不把契约重新复制成字符串表。

```text
domain contracts + domain validators
                 |
                 v
          composition.py
           /           \
WorkflowDefinition   ValidationRegistry
           \           /
                core
```

## 4. 与 knowledge-guided-driver-port 的阶段映射

| Skill phase | DPF gate |
|---|---|
| 0 迁移 envelope | `project_init` → intake 四阶段 → `repository_acquisition` |
| 1 证据、运行环境和 baseline | `repository_acquisition` → `evidence_closure` → `environment_recovery` → `knowledge_base` |
| 2 目标平台研究 | `target_platform_study` |
| 3 来源范围闭包 | 合入 `migration_contracts`：直接读源码，按问题验证 |
| 4 编码前迁移合同 | `migration_contracts` |
| 5 公开测试筛选与映射 | 同一个 `migration_contracts` 调用和报告 |
| 2–5 分析证据核验 | `analysis_review`：封存设计阶段前由独立审查者合并核对原文、契约与测试断言 |
| 6 目标框架能力补齐 | `target_framework_enablement`：实现合同确认的缺失接口，封存目标快照 |
| 7 Rust 设计与实现 | `driver_implementation`，只消费目标框架快照，含测试适配 |
| 8 目标合规复核 | 工作者在实现阶段自检，无独立模型节点 |
| 9 runtime artifact 与公开 QEMU ladder | `artifact_preparation` → `public_qemu_validation` |
| 10 归因与窄修复 | 原工作者归因、自检与窄修复；独立检查者核对功能和原始证据，最终报告替代程序汇总 |
| 11 盲测候选封存 | 仅 blind mode 增加 candidate sealing 与 export/transfer |
| 12 最终证据审计 | 开发模式为 `final_evidence_review` 独立功能审查；blind mode 另保留 `completion_audit` |

`PROSPECTIVE_BLIND` 在迁移前增加 public bundle/commitment binding；
`POST_HOC_SEALED_BLIND` 只在候选封存后导出 opaque digest。curator、evaluator、auditor 使用
独立 DAG，迁移工作流没有私有 assertion、seed、checker 或私有结果入口。

## 5. 唯一成功路径

成功阶段只能提交 `FileArtifact | GeneratedArtifact`，并经过：

```text
typed artifact
  -> domain validator
  -> SHA256 CAS
  -> optional stage bundle validator
  -> Project.finalize_stage
  -> private _RunPersistence._commit_validated_stage (one SQLite transaction)
       -> attach artifact occurrences
       -> verify complete required-output set
       -> set PASS
       -> append artifact/stage ledger events
       -> refresh newly READY stages
```

`Project.complete(PASS)` 和内部 `_RunPersistence.complete_stage(PASS)` 均拒绝直接成功；普通
`record_artifact` 只能给 `RUNNING` 阶段追加输出且不会改变状态。数据库事务中任何一步失败都会
回滚 artifact occurrences、状态和 ledger。CAS 可能保留未引用的内容块，但它不能形成阶段证据
或 PASS。公开 API 不暴露 raw store 或绕过 validator 的成功提交入口。

产物内容身份与阶段 provenance occurrence 分开持久化：

- `artifact_contents` 以 `(digest, kind)` 保存可去重的不可变内容身份；
- `stage_artifacts` 以独立 `occurrence_id` 和 `(stage, direction, ordinal)` 保存每次出现的 source；
- 两个 translation unit 即使生成相同 bytes，也保留两个 source/ordinal occurrence；
- cardinality、bundle consumption 和 ledger projection 都按 occurrence 计数，不按 digest 去重。

项目重开时会核对 schema、完整 ledger hash chain、ledger 对 stage/occurrence 的投影、全部阶段规格、
required-output cardinality、foreign key、CAS canonical path/size/digest，以及 `project.json` 与数据库和
冻结 project manifest 的一致性。任何漂移都 fail closed。

输出契约区分两类产物：

- `required` 是阶段成功声明；每项显式携带 `EXACTLY_ONE` 或 `ONE_OR_MORE` cardinality，只有完整
  bundle 一次性校验、登记后才能置为 `PASS`；
- `auxiliary` 是 attempt、Prompt、Codex 原始结果和事件等过程证据；可在 `RUNNING` 中追加，但永远
  不能补足 required output 或独立触发成功。

源码理解、迁移契约和测试计划由同一工作者在 `migration_contracts` 完成。编译器证据按具体问题触发，
记录在现有报告，不再自动生成全量索引或复建验收。输入只有冻结原始材料、目标研究和环境证据。
参见 [源码与设计](SOURCE_DESIGN.md)。

## 6. JSON、CLI、SQLite 与 CAS 边界

- CLI 使用 `argparse` 在入口把 role、mode、domain、backend 和 outcome 解析成领域类型；sandbox
  由阶段策略推导，CLI 不接受覆盖值；
- JSON validator 在受控文档入口把封闭状态解析一次，非法值不会进入领域决策；
- SQLite 只保存 enum/value object 的 canonical `.value`，读取阶段时由当前
  `WorkflowDefinition` 恢复 typed key 并核对持久化依赖及 required outputs；
- CAS 保存不可变 bytes 和 canonical kind value；读取路径只由 digest 推导，并拒绝持久化
  `cas_path`、size 或 digest 漂移；`Project` 以 typed artifact key 查询和验证；
- `ValidationRegistry.parse` 对未知 artifact fail closed，CLI 不能注册任意 kind；
- Prompt Pack 和 Skill 文本可以频繁修改。每次 Job 保存实际 Prompt 与摘要用于复现，但普通开发
  不以旧 hash 阻止更新；只有正式 held-out batch 在实验契约中冻结选定版本。

Codex 文件权限由执行策略统一选择：开发工作者使用 `danger-full-access`，阶段目录用于组织产物，
不是安全隔离。非开发模式按角色限制可写目录并保护冻结基线。模型 Prompt、
原始响应和事件始终只登记为过程证据；CLI 不提供从模型响应直达 required output 的入口。所有
required bundle 都必须由明确的领域 adapter 解释、生成 typed artifacts、验证并一次性 finalize。

`INDEPENDENT` 不是同一 thread 中切换 prompt 的别名。此类阶段只能在 curator/evaluator/auditor
专属项目 DAG 中启动；exec backend 强制 `--ephemeral`，SDK backend 每个 Job 调用 `thread_start`，
CLI 不接受 thread ID 或 resume 参数。正式独立性还要求新进程/上下文、独立工作区和凭据以及正确的
公开/私有材料挂载；程序内的 fresh thread 只是一项必要条件，不能单独证明组织独立性。

## 7. CLI adapters

根 `cli.py` 只负责命令组装、dispatch 和统一错误边界。完整开发迁移只由 `dpf port run` 驱动；
源码闭包、结构化分析和迁移阶段由 `PortRunner` 直接调用领域服务，不再暴露第二套阶段 CLI。
其他 bounded context 的维护命令仍在各自 `cli.py` 中完成参数转换，例如 environment 的盘点、
计划登记和执行，以及 knowledge 的材料登记、probe、Skill 生成和 bootstrap 编排。

## 8. 数据与信任域

开发/迁移、策展、评测使用独立项目目录、凭据和 Codex 上下文。程序可以联网获取公开且与任务
相关的资料；网络可用性不是独立性门禁。真正的边界是迁移域不能读取 PEA、私有 harness、seed、
checker、fault schedule 和私有结果。

跨域只传递显式 bundle：

- 策展域 → 迁移域：公开任务包、PMC、commitment；
- 迁移域 → 评测域：不可变候选包及摘要；
- 评测域 → 报告域：全部任务完成后的冻结结果。

阶段执行状态为
`PENDING/READY/RUNNING/WAITING_FOR_USER/PASS/FAIL/BLOCKED/INCONCLUSIVE/NOT_APPLICABLE`；
证据结论另用
`VERIFIED/INFERRED/PLANNED/NOT_RUN/NOT_APPLICABLE/BLOCKED/FAIL/PASS`。
`NON_INDEPENDENT` 和 `HARNESS_INVALID` 是评测分类，不能被普通失败覆盖。
