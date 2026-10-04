# KNOWLEDGE_BASE_GUIDE.md 内容核对

规范：`../C-kernel-to-Rust-upstream/KNOWLEDGE_BASE_GUIDE.md`，2026-10-02。本文件补充对两套 Skill 的审计，不能把“检索器已实现”称为“具体任务的材料已经备齐”。

## 内容准备状态

知识库随具体迁移任务生成，不是一份预先完整建立的 Linux/Asterinas/所有设备百科。控制器取得完整固定 Git 仓库，但只有受控材料清单中选定且可索引的文件进入搜索。完整 checkout 存在，不代表所需定义/调用点已经可检索。

| 指南内容 | 现有机制及本轮落实 | 当前能声称什么 |
|---|---|---|
| 硬件手册、总线、寄存器状态机、恢复/边界 | HardwareFacet + 受控外部获取与 authority/provenance | 支持按具体设备采集；本轮未准备完整设备硬件库 |
| C 驱动完整相关闭包、共享定义、配置、回调、源测试/框架 | SourceFacet/TestFacet、固定源仓、分析原文追踪与按需补 corpus | 支持采集/修复；是否完整须逐任务核查，入口文件命中不能证明完整 |
| 目标 API 定义与调用点、生命周期、并发、错误、安全/风格、相似驱动和运行材料 | TargetFacet、固定目标仓；analysis 按需 focused 查询重放；工作者原文自检 | 已有执行机制；空探测列表允许；查询成功不能证明每项语义覆盖 |
| QEMU 模型、参数、后端、QMP/qtest、事件/故障/限制 | QemuFacet、固定 QEMU 仓、原文与运行证据分离 | 支持按设备收录；没有泛化的全部模型行为证明 |
| 来源、版本、时间、hash、许可证、派生物与页码 | MaterialRecord、origin/authority、PDF 派生与 page_map、索引完整性 | 有强制结构/身份校验；PDF 视觉确认和内容解释由工作者负责 |
| 原文检索与扩大范围 | search/show/search-batch/rag，record/path/domain 过滤，直接固定仓搜索与受控补充 | 已实现；无命中不等于无能力；向量相似不证明相关性 |
| 自动生成 KB Skill 九项最低契约 | data/project-kb-skill.md；本轮补明确决策触发、契约引用查找、静态/推断/运行区分、冲突和重建职责 | 内容进入实际生成 Skill，不依赖额外 reviewer |
| 根目录指南进入工作流 | 本轮将原文复制到 prompt pack 的 knowledge-base-guide.md，并加载到 evidence_closure | 过去只带 skill/ 的遗漏已补；使用包内副本，修改须明确更新 |
| 知识指导具体实现与测试 | 同分析报告保留 contract ID → record/chunk/original → invariant/API/assertion/blocker | 工作者责任；未做确定性语义蕴含证明，避免另建巨型契约库 |

全量结构化 C 导出仍遵循既有用户授权差异，见 SOURCE_DESIGN.md。本轮不因指南重新恢复该昂贵阶段。没有新增知识总结代理或逐轮 reviewer。

## 实际语料证据

本轮真实 embedding 开发探针仅使用固定 Asterinas commit 的同步与 PCI Git blobs：86 chunks、6 个问题。它不能证明硬件、Linux 源闭包、QEMU 模型或整个目标平台材料已经备齐；离线测试中的“覆盖全部主题”是明确的合成 fixture。

真实实验应在创建任务后检查实际 materials manifest、evidence gap register、inventory 和 target knowledge observations；解释缺口是否影响契约，并通过受控证据修复补齐。若缺少具体设备或实际 workspace，不能预先宣布四域内容完整。本轮不修改任何旧实验语料或历史结论。

本轮打包指南原文 SHA256：`82d18b2b14290329791a2a4ecfab2c7bf7b97e57f5451ad29092e5c1b1aaf2c7`。

现行协议遵循后续成本要求：按需检索，允许直接查阅已知源码；没有固定七类检索配额。
这与上游指南的逐项、逐阶段重查存在明确差异，不能称为逐字一致。
经验工具、验收后发布及下一任务入口见 [共享知识库](SHARED_KNOWLEDGE.zh-CN.md)。
