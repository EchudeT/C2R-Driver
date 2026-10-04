# Skill 规范追踪矩阵

更新于 2026-10-02。本轮核对上游两套迁移 Skill 与全部 12 个 reference；详细发现、修复及兼容性见 [执行对齐记录](SKILL_ALIGNMENT_2026-10-02.zh-CN.md)。

`MECHANICAL` 表示控制器能验证对应身份、查询或执行，不表示语义正确；`WORKER` 表示工作模型必须取证、自检，并由适用验收测例检验，内容不是程序确定性证明；`OPTIONAL_REVIEW` 为可关闭的辅助；`USER_POLICY_OVERRIDE` 为明确授权的差异。以下状态不等于真实驱动实验已完成。

## 迁移入口：open-kernel-driver-port

| 要求 | 实现/承担者 | 状态与边界 |
|---|---|---|
| 采集前唯一驱动、设备/总线范围和合并消歧 | intake 阶段、持久化问题与冻结 envelope | MECHANICAL；候选证据解释由 WORKER |
| 固定 source/target/QEMU commit、原始 baseline 与可写树分离 | acquisition/repository、baseline、材料清单与 bundle validator | MECHANICAL；本地原仓只读导入 |
| 源/目标/硬件/QEMU/环境证据闭包 | acquisition facets、typed proposal、provenance/gap | MECHANICAL 身份与枚举检查；充分性为 WORKER |
| 选实际可运行环境路线并保留失败 | environment discovery/execution/validation | MECHANICAL 命令/进程/日志；路线选择为 WORKER，不能把启动等同驱动工作 |
| 可检索、精读、完整性、重建的本地 KB Skill | knowledge bootstrap/index/skill_generation；BM25 与本地 embedding RAG | MECHANICAL；生成模板来自包内 data/project-kb-skill.md |
| 目标知识质量检查、弱检索修复与复测 | target study 同报告 + probes.json；knowledge/probes.py；证据缺口回到 evidence_closure | MECHANICAL 查询/原文范围重放；解释和 N/A 为 WORKER 自检；不宣称全部目标知识覆盖 |
| 目标基线构建、启动和观测链路复用 | platform profile/executor/guest/service；环境阶段门禁 | MECHANICAL；Asterinas x86_64 ISO/KVM 已实测，非通用平台能力；不代表迁移驱动通过 |
| 完整 handoff，包含环境、证据、知识入口 | migration/handoff.py | MECHANICAL 绑定当前产物；有用内容由 WORKER |

## 迁移执行：knowledge-guided-driver-port

| 要求 | 实现/承担者 | 状态与边界 |
|---|---|---|
| 目标画像、API 原文、相似驱动路径 | analysis-task 与 target study，质量记录绑定报告和语料 | WORKER；可选 analysis reviewer 复核，不是通过的必要模型角色 |
| 源入口、共享定义、配置、回调、生命周期与测试闭包 | 同一 analysis 报告 | WORKER；从原文追踪，不能仅靠 API 名猜测 |
| 全量结构化 C 事实 | 以具体问题的编译器探测替代 | USER_POLICY_OVERRIDE，见 SOURCE_DESIGN.md，不恢复旧强制阶段 |
| 四域契约和源测试七类筛选、保留 stimulus/oracle 与来源 | migration_contracts；analysis-task；contracts taxonomy | WORKER；契约/测试计划引用同一报告，不额外复制 |
| 目标框架最小修改、必要性/替代/安全/回滚 | delivery-task；统一 implementation 快照（旧项目保留 framework checkpoint） | WORKER 判断；MECHANICAL 快照与归属 |
| 编码后及相关修复后目标规则合规 | delivery-task/repair 明确当前工作者责任；同报告更新 | WORKER；首次包装前查规则，后续按改动重查原文，不每轮固定检索 |
| 源/新增/适配公开测试不能冒充独立私测 | analysis-task、公开工作报告与实际 receipts | WORKER 来源解释；公开测试作者身份不因 PASS 改变 |
| 当前制品、源码与 QEMU 运行身份 | implementation、artifact_preparation、public_qemu | MECHANICAL 快照/制品/脚本/trace/logs；不证明全部设备行为 |
| 归因、保留失败、修复受影响检查及停滞保护 | repair routing、checker decision、store 依赖失效 | MECHANICAL 身份与进度；WORKER 根因与修复；开发模式可据反证修订早期前提 |
| 按契约/测试记录状态和范围限制 | 同一工作报告与验收收据；可选 final_evidence_review | WORKER + MECHANICAL 执行；OPTIONAL_REVIEW 不替代测试 |
| 封存后汇总实际证据，公共 harness 不升级为语义证明 | completion_audit，当前 PUBLIC_HARNESS/outcome 协议 | MECHANICAL；仅封存路线，仍不等于私有盲评 |

## 独立评测：blind-c2rust-driver-evaluation（历史追踪，本轮未完整复核）

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
- 每次新增阶段都必须同时补上 Skill 来源、最小输入/输出契约、必要门禁、失败状态和回归测试。
