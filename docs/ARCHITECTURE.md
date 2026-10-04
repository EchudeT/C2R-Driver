# 架构设计

## 平台配置归属

操作者通过启动参数提供镜像与加速器，控制器把它们保存在 `ProjectConfig`，在模型调用前检查
配置齐备，续跑沿用已存值。`driver_checks.platform` 的 `bootstrap` 不接受模型选择的环境参数；
底层 prepare 也只读取项目配置。Docker 是构建和 QEMU 的执行环境，宿主信息只用于诊断，
不用于推断 TCG/KVM。现有镜像身份、OVMF、执行收据和失败保留机制继续使用。

## 最后工作包交付

预置测试的case集合同时用于实现完成检查与公开验收；分析正文不能扩张必需运行测试。
最后一个工作包的done保存模型的简短源码论证及验证限制，直接进入原实现捕获与14/15控制器
验收。没有新增提交门禁，不固定启动另一个模型收尾回合；公开检查通过后也不再要求模型
重新确认已通过的运行记录。实现阶段可复用历史结果；15完整执行一次同一最终代码快照及
产物上的所有预置测试。最终整组通过后，恢复流程直接复用该批结果，临时路径、PATH、
日志和报告差异不触发复测。详见[交付边界](FINAL_PACKAGE_HANDOFF.zh-CN.md)。

## 1. 架构约束

DPF 是证据驱动的工作流控制器，不是拥有全局权限的长对话。其不可弱化的约束是：

1. 必需证据和角色隔离映射上游 Skill；允许将同一工作者的实现步骤合并为一个原子阶段；代码整洁不能绕过证据门禁；
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
| 3 来源范围闭包 | 合入 `target_platform_study`：直接读源码，按问题验证 |
| 4 编码前迁移合同 | 同一次联合分析；`migration_contracts` 仅由控制器登记 |
| 5 公开测试筛选与映射 | 同一个 `target_platform_study` 调用和报告 |
| 2–5 分析证据核验 | 工作者自检；`analysis_review` 默认关闭，可显式开启 |
| 6 Rust 设计与实现 | 统一 `driver_implementation`：框架适配与驱动集成一次验收，保留最小目标变更依据；当前分支不兼容旧冻结任务 |
| 7 目标合规复核 | 工作模型在第一次包装前与受影响修复后自检、按需复查目标原文；无独立模型节点 |
| 8 runtime artifact 与公开 QEMU ladder | `artifact_preparation` → `public_qemu_validation`；复用当前制品与收据 |
| 9 归因与窄修复 | 原工作者修复具体原因，测试受影响行为；开发模式允许反证推翻分析前提 |
| 10 最终证据审计 | 工作报告逐契约/测例记录；可选 `final_evidence_review` 增加独立检查 |
| 盲评扩展（不属于上述两套迁移 Skill 的阶段编号） | 封存、transfer/export、`completion_audit`；保持公共执行与独立私测的界限 |

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

源码理解、粗路线、迁移契约和测试计划由同一工作者在 `target_platform_study` 联合完成；
`migration_contracts` 是控制器登记检查点，不再启动独立研究。编译器证据按具体问题触发，
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
源码理解、按需语义探测和迁移阶段由 `PortRunner` 直接调用领域服务，不再暴露第二套阶段 CLI。
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

目标研究现在要求与同一报告绑定的 `target_knowledge_quality`。它重放查询和核对原文，不证明语义正确；工作模型自检和实际测例承担质量责任。RAG 与 reviewer 开关的边界见 [对齐记录](SKILL_ALIGNMENT_2026-10-02.zh-CN.md)。

07 负责固定材料，后续研究负责语义分析；08 的通用容器执行包装可由控制器生成。失败 attempt 在返回原因前保存，不走 PASS 登记。配方复用要求版本/镜像匹配及当前执行，不能导入旧验收。接口与验证见 [前置阶段优化](BOOTSTRAP_OPTIMIZATION.zh-CN.md)。

新任务默认启用 `unified_implementation`；开发 CLI 同时默认启用 `behavior_scheduling`，
每轮只推进一个可观察行为及其必要框架适配、测试和清理；不再单列框架使能或全面能力探测。实验配置的旧分支不用于新路线任务续跑。框架与驱动产物在同一事务提交，校验同一源码、worktree 和报告；下游仍验证制品身份和实际运行。详见 [合并实现与去重规则](UNIFIED_IMPLEMENTATION.zh-CN.md)。

## 平台执行层

模型长命令入口统一为阻塞式 `driver_checks.platform`，复用原执行器、身份检查和清理。
依赖解析先于构建输入冻结；按需组件接入导航引用当前源码；guest shell 条件使用带新标记的
退出码断言。进展由监视器读取，不由模型轮询。详见 [交互重构](PLATFORM_WORKER_EXECUTION.zh-CN.md)。

`platform/profile.py` 定义固定平台命令，`executor.py` 隔离 Docker/构建执行，`guest.py` 提供有时限的 QMP/串口传输，`service.py` 绑定项目、源码、镜像与收据。新 Asterinas 项目在环境阶段验证干净基线，后续实现直接调用同一执行层；设备刺激和断言保留给迁移工作。平台通过与驱动验收分离。当前能力、接口与真实验证边界见 [PLATFORM_EXECUTION.zh-CN.md](PLATFORM_EXECUTION.zh-CN.md)。

## 实现内局部前提修正

统一逐功能 developer-evidence 运行中，目标 API/平台设计选择的修正由当前实现行为拥有，
`local_adaptation` 保存问题证据并续接原会话，不级联撤销全部后继阶段。
这不是变更冻结合同、环境或验收标准的权限；它们仍走显式前置修复。
行为完成与最终验收分离，详见 [局部适配](LOCAL_ADAPTATION_COST.zh-CN.md)。

## 按需实现与基础设施交接（2026-10-03）

实现回合实际接收 `tool_runtime.platform_execution` 的 build/format/run-case 命令和 JSON 接口。当前行为使用专用任务模板；收齐全部行为后才切换完整交付。检查返回有界反馈，完整日志和收据仍由控制器保留。分析检索采用显式 focused 规格，七主题是可选分类而非调查配额；空检索不声称语义覆盖，原文/报告身份校验继续生效。详见 [实现成本审计](IMPLEMENTATION_COST.zh-CN.md)。这不改变冻结范围、当前行为调度或最终接受条件。

环境执行已完成[采集重构与 OVMF 兼容修复](OVMF_AND_EXECUTION_REFACTOR.zh-CN.md)：托管容器内跟踪替代短命进程轮询，基础设施故障不进入付费返修。OVMF 的 pvpanic BAR 兼容补丁作为显式本地派生镜像提供，原 QEMU 与目标内核不变。真实平台检查通过，端到端驱动成本仍待新实验。

## 分析上下文与功能边界

新项目将环境、分析分别置于持久专用会话，分析首轮使用显式输入而非采集历史。
功能范围和设备身份分开冻结；目标 kernel 接入与 callback harness 的验证层级不能混用。
前置分析停止于源义务、目标承担关系、必要前提和验收设计明确，局部实现调查由当前行为承担。
实现调度与最终验收不变，详细规则及能力限制见 [分析边界](ANALYSIS_BOUNDARY.zh-CN.md)。


## 当前路线协议

三个宏观阶段及逐步职责以 [STAGE_GUIDE.md](STAGE_GUIDE.md) 为准。
`target_study` 接受报告、路线索引和检索来源收据；契约和测试正文引用同一报告。
`migration/route_model.py` 处理纯引用关系，`route.py` 负责保存和局部修订，
`behavior.py` 直接从分析图选取一个行为。粗路径节点不会自动变成实现单元。

实现候选绑定最新路线记录，普通代码编辑不触发路线重审。明确路线修订才按行为关系
更新完成进度；改变当前行为的目标不能借旧回合的 done 完成新目标。最终接受仍由
现有构建、运行和固定断言决定；引用关系本身不证明语义覆盖。

托管 08 使用平台已有基线收据，取消强制模型设备探针。按需 `analysis probe` 绑定
联合分析中的一个问题，使用固定执行器。知识积累选取已有分析段落和证据，不新建
知识写作任务；共享库写入失败只记录未发布，不能使已接受实现返工。
详见 [ROUTE_GUIDED_ANALYSIS.zh-CN.md](ROUTE_GUIDED_ANALYSIS.zh-CN.md)。

## 逐行为工作包与平台工具

[工作包与通用工具优化](BEHAVIOR_COST_TOOLS.zh-CN.md)只改变提示投影和按需执行能力。
联合分析选择完整工作目标；简单驱动可只有一个包，多个义务或内部步骤不强制拆分。
控制器维持每轮一个工作包、同一会话和最终独立验收。
正文 D 引用由提示渲染器生成，不是新的计划格式或模型交付。组件 scaffold 属于实现阶段的可选操作；
内联用例继续通过同一平台执行器记录真实观察。当前行为必要检查不推迟到最终验收。
这些工具不生成设备语义或质量真值，尚未用新付费实验验证成本收益。

托管平台基线验证后自动安装 smoke/public 入口；实现阶段 register_case 保存设备用例、包装及
既有 experiments.json 清单。开发按 ID 执行，最终消费完整清单，统一使用逐用例身份绑定收据。
不新增计划格式或模型阶段，不把临时探测自动算入验收。见[自动测试入口](PREPARED_TEST_ENTRYPOINTS.zh-CN.md)。

### 固定公开测试的责任分工

新的 Linux pvpanic-pci → Asterinas 全范围任务预装三个公开用例。控制器固定刺激、断言和完整
最终执行集合，模型只适配调用接口、实现并修复驱动；一个工作包可以包含全部场景。原生入口与
接口调用覆盖分开报告，不缩减源范围，不增加 reviewer。其他驱动继续用已有登记机制。详见
[固定公开测试](PREPARED_PUBLIC_TESTS.zh-CN.md)。

## 共享经验工具

分析和实现通过可选 `knowledge_learn(lesson, conditions, sources)` 保存已有发现，
控制器归档源码，15验收通过后发布。进度工具仍只有 status/note；没有经验交付清单。
读快照与发布目标分别冻结，分析入口只提供少量匹配经验；查询按需，写回失败不影响验收。
见 [共享知识库](SHARED_KNOWLEDGE.zh-CN.md)。
