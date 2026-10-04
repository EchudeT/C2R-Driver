# 上游 Skill 执行对齐记录

本轮对照 `../C-kernel-to-Rust-upstream/skill/` 的两套迁移 Skill 及其全部 12 个 reference，检查 v1 实际控制器、提示、验收与证据。实现分支为 `feature/provenance-rag`。独立子 agent 完成只读审计；未干预旧实验。

## 方法与职责

迁移方法主体是工作模型的分析、实现、自检和实际验收测例。分析 reviewer 与最终 reviewer 是开发路线的可选辅助；不是知识质量或正确性的唯一承担者。关闭它们仍需原文证据、契约与测试来源、当前制品身份、实际执行收据和工作者自检，不得将模型声明或检索成功当作测试通过。当前默认开关保持原设置，可通过 `--no-analysis-review --no-final-evidence-review` 创建无 reviewer 的开发实验。

封存／盲评路线当前仍要求最终 reviewer，这是既有协议限制；不等同于独立私有测例评估，也不应作为本文方法创新。后续若该路线也取消 reviewer，须同步修改候选必需产物和审计依赖，不能只删提示词。

## 已落实的差异

| 审计发现 | 本轮落实 | 证明边界 |
|---|---|---|
| 原 KB 只有词法检索，没有 embedding RAG | BM25、本地 Sentence Transformers、倒数排名融合、有界原文片段、生成工具入口；绑定模型、推理配方及语料身份 | 上游本身不强制向量数据库；这是符合原文契约的增强，不是补齐一个上游必需服务 |
| bootstrap 就绪不代表目标知识质量检查，study 仅接收 Markdown | 同一分析附紧凑 `.probes.json`；提交时冻结；控制器重放七类目标查询、验证原文及范围，生成 `target_knowledge_quality`；bundle 重新计算，阻止直接 finalize 绕过 | `RETRIEVAL_VALIDATED` 只证明检索与原文身份；`SEMANTICS_NOT_MECHANICALLY_VERIFIED` 明确不证明相关性、完整性和 API 推论 |
| 实现和修复后复核目标规则无人明确负责 | 实际加载的 delivery/repair 提示明确模型自检、必要原文复查、目标变更必要性/替代方案/影响/回滚；更新同一报告 | 不增加固定 reviewer、报告阶段或每轮 KB 查询；第一次包装前复核，此后按变化检查 |
| 最终 reviewer 目标绝对禁止回查分析前提 | 仅启用 reviewer 时：具体反证允许回查关联原文，申请最小受影响修复；修正 phase 10 引用 | 没有反证不重复研究，不能静默放宽 oracle |
| 封存汇总读取旧 `TARGET_DRIVER_ON_QEMU`、`decision` | 使用当前 `PUBLIC_HARNESS` 收据、runtime digest/trace/logs 和 review `outcome`，增加直接数据依赖及 bundle 核验 | 默认开发路线不运行此汇总；harness 运行不能自动提升为设备语义正确或真实硬件验证 |
| 知识库、阶段及追踪文档过期 | 更新 KNOWLEDGE_BASE、ARCHITECTURE、WORKFLOW_ALIGNMENT、SOURCE_DESIGN、README 和追踪表 | 账本阶段不等于模型调用；文档不代替实际执行证据 |

## 明确保留的用户授权差异

全量 AST/CFG/CPG、独立源码闭包阶段和 C-facts 专用协议已在 `SOURCE_DESIGN.md` 记录用户授权删除。本轮不恢复。源码范围、配置、回调和行为覆盖仍由工作模型从原文追踪；宏、布局和条件编译疑点按需使用编译器证据。不能把该差异说成漏实现，也不能声称与上游逐字相同。

阶段连续执行、合并分析、收据复用、具体反证下最小返修、独立审查开关原本已经存在。本轮不是重新增加这些能力。源测试七类 taxonomy 及原始 stimulus/oracle 的职责已有承接；机械 UTF-8 校验不能证明其语义内容完整，相关责任留在工作模型自检及实际验收。

## 验证与尚未建立的结论

- 离线测试验证查询重放、伪造/过期证据拒绝、提交后 sidecar 变化、审计归因和 reviewer 关闭后的交付。使用合成仓库和执行替身，不是实际驱动结果。
- 真 embedding 开发探针：固定 Asterinas commit、86 chunks、6 个预先列出问题；文件 hit@5 为 BM25 5/6、混合 6/6。一个 RCU 问题的预期文件由第 1 降为第 4。详见 `RAG.zh-CN.md` 与原始统计；不是 held-out 或语义正确率。
- 未启动付费模型或真实驱动迁移实验，不能声称整体翻译质量、时间和费用已改善。
- 本次完成两套迁移 Skill 的执行差异审计及上述修复；未声称整个独立盲评部署、组织隔离或硬件评价已实现。

新增 study 必需产物和封存 audit 依赖会改变工作流 schema。请新建 workspace 验证；旧 workspace 保留旧代码版本运行和历史证据，不自动补 PASS、迁移账本或用新版本强行续跑。

后续用户指出根目录 `KNOWLEDGE_BASE_GUIDE.md`；已单独补查并接入采集提示，内容与实际语料状态见 [指南核对](../knowledge/KNOWLEDGE_BASE_GUIDE_ALIGNMENT.zh-CN.md)。本轮离线全套在监视器与后续提示补充前为 272 项通过；监视器另以离线日志测试及真实 PTY 分栏演示验证。
