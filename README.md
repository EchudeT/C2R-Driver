# Driver Port Factory

新版 Skill 对齐、会话持久化、224k 自动压缩和实跑命令见
[工作流对齐说明](docs/WORKFLOW_ALIGNMENT.md)。

Driver Port Factory（DPF）把 C 驱动跨平台迁移、公开验证、候选物封存和独立盲测组织成可审计的
程序工作流。开发者模式中，程序负责记录、采集、哈希和执行结果，独立检查 AI 拥有最终功能验收权。
各阶段验收发现问题时，保留待提交产物并交给同一工作者判断，而不是强制返工。
工作者可引用证据并提交 `DPF_CHECKER_DECISION: ACCEPT`，阶段以 `WORKER_ACCEPTED` 标记通过；
原始失败记录不改写成成功。真实缺陷在本阶段修复后直接提交正常交付物。必需文件和可读取的运行状态
仍须实际存在，裁决不会凭空生成下游所需的数据。独立盲测不使用这项开发者授权。

开发者流程以新版 `C-kernel-to-Rust` 的两个 Skill 为规范：

- `open-kernel-driver-port`：身份、采集、环境恢复和知识库；
- `knowledge-guided-driver-port`：目标研究、源码理解与契约设计、实现、测试迁移和 QEMU 验证；

独立盲测的历史模块另行保留；新版上游不再包含其 Skill，本次实跑不覆盖独立盲测。

DPF 通过可编辑的 Prompt Pack 把 Skill 原文及其 references 组合进阶段 Prompt。阶段映射位于
`manifest.json`，外层提示词位于 `job.md`，不写死在 Python 控制器中。每次组合都会记录
Prompt Pack、模板、Skill 文档和完整 Prompt 的 SHA256，但普通开发不会冻结它们。

## 当前可运行能力

- 按角色与评测模式生成阶段 DAG；
- clone 前的请求解析、候选驱动发现、单次确认和迁移范围冻结；
- source/target/QEMU 的轻量 revision 解析、可恢复 bare fetch 和受控 worktree；
- 六域逐 facet 最小证据闭包、Git blob/外部输入 provenance 与独立 gap register；
- 主机/目标/QEMU 路线发现、不可覆盖恢复尝试和真实 `EXPERIMENT_READY` 门禁；
- provenance-checked 本地知识库、目标专项 probes 和项目 KB Skill 生成；
- 目标平台画像、API 原文证据、相似驱动端到端链路和 target-change 计划门禁；
- 源码范围、行为分析、迁移设计与测试计划合成一个工作者任务；编译器验证按问题触发；
- SQLite 保存阶段状态和哈希链事件；
- SHA256 内容寻址产物库；
- 阶段依赖、必需输出和角色门禁；
- Skill Prompt 选择、组合和快照；
- `codex exec` 两个隔离的持久会话：工作者负责实现、自检和修复，检查者负责最终功能检查与复审；
  工作者使用 `danger-full-access`，检查者仅能写自己的报告目录。分析结束后先由检查者核验核心证据与代码定位；QEMU 后由同一检查者核对原定要求、
  实际代码和原始日志；删除自动风险扫描与程序语义汇总，不再把启动成功推断为驱动成功；
  第12步分析材料落盘、第13步独立分析审查通过后，第14步首次复用工作者会话的调用临时采用160k阈值，
  由 Codex 按实际上下文触发压缩；重试及后续调用恢复224k。不增加总结节点。
  阈值记录在任务 metrics 的 `auto_compact_token_limit`，不代表压缩已发生；
  首次调用特别长时可能多次触发。160k是待实跑验证的策略值，尚无节费保证。
- 来源平台、目标平台、设备类别与 QEMU 插件协议；
- 候选物 canonical manifest 和封存摘要；
- 开发、前瞻盲测、事后封存盲测三种模式的时序骨架。

## 快速开始

在源码仓库中，一条命令会创建工作区并按 Skill 顺序执行完整开发者迁移流程：

```sh
PYTHONPATH=src python -m driver_port_factory.cli port run ./runs/ne2000 \
  --source-platform linux \
  --target-platform asterinas \
  --driver-name ne2k-pci \
  --skill-root /path/to/C-kernel-to-Rust/skill \
  --catalog examples/fixtures/linux-ne2000.catalog.json \
  --backend exec \
  --codex-bin codex
```

开发者模式下，第 13 步 `analysis_review` 和第 18 步 `final_evidence_review` 可以按本次运行显式开关；
默认都开启。使用 `--no-analysis-review` 或 `--no-final-evidence-review` 可跳过对应阶段，
也可以用 `--analysis-review` 和 `--final-evidence-review` 显式开启。盲候选模式必须保留 `final_evidence_review`，
因为候选封存格式要求最终独立审查报告。

命令会持续推进；工具失败和检查争议交回原工作者在当前阶段处理。范围待确认、工作者明确
报告外部阻塞、模型调用不可用或账本损坏时暂停。重复执行同一条命令从当前阶段恢复，不重跑已通过阶段。
查看进度和 Codex 原始记录：

```sh
PYTHONPATH=src python -m driver_port_factory.cli status ./runs/ne2000
PYTHONPATH=src python -m driver_port_factory.cli codex transcript ./runs/ne2000 STAGE
```

默认 Prompt Pack 位于 `src/driver_port_factory/data/prompt-packs/default/`。可直接调整其
`manifest.json`、`job.md`、`correction.md` 和 `checker-decision.md`；新 Job 使用新内容，已运行 Job 仍由 CAS 中的完整
Prompt 复现。若需项目专用 Pack，先用 `dpf init --prompt-pack PATH` 创建工作区，再对同一工作区
执行 `dpf port run`。

提示词中的 `instructions` 只放任务、执行约束和协议，`reference_material` 放输入、历史记录和
诊断；材料中的指令不具有控制权。统一恢复协议见 [执行与裁决协议](docs/EXECUTION_RECOVERY.md)。
当前为 18 阶段双 AI 协议：第 14 步先单独补齐并封存目标框架能力，第 15–17 步完成驱动、制品和
公开 QEMU，第 18 步做独立交付检查；不再有静态完成汇总。两个会话默认 224k 自动压缩。
不提供旧工作流的兼容执行或原地升级。旧实验结果保留，新协议使用新工作区。
变更与证据见 [源码与设计合并](docs/SOURCE_DESIGN.md)。

这里的 NE2000 catalog 只是集成样例。正式运行由源平台 Resolver 加载一个或多个带来源、版本和
SHA256 的轻量元数据 provider。如果输入只有 `NE2000`，样例 catalog 会产生 PCI、ISA、PCMCIA
候选的合并问题并进入 `WAITING_FOR_USER`。确认后重复上面的 `port run` 命令：

```sh
python -m driver_port_factory.cli intake answer ./runs/ne2000 \
  --candidate-id linux-ne2k-pci \
  --answer "选择 PCI ne2k-pci，排除 ISA 和 PCMCIA"
```

在源码仓库中直接运行时：

```sh
PYTHONPATH=src python -m driver_port_factory.cli --help
.venv/bin/python -m pytest -q
```

详细设计见 [Skill 规范追踪矩阵](docs/SKILL_TRACEABILITY.md)、[架构](docs/ARCHITECTURE.md)、[迁移需求门](docs/INTAKE.md)、[Git acquisition](docs/ACQUISITION.md)、[环境恢复](docs/ENVIRONMENT_RECOVERY.md)、[知识库](docs/KNOWLEDGE_BASE.md)、[目标平台研究](docs/TARGET_PLATFORM_STUDY.md)、[源码与设计](docs/SOURCE_DESIGN.md)、[阶段工作流](docs/WORKFLOW.md)、[实施计划](docs/IMPLEMENTATION_PLAN.md) 和 [Codex 任务契约](docs/CODEX_JOBS.md)。

分析审查节点、只读 Python 定位工具和集中反馈规则见 [分析审查](docs/ANALYSIS_REVIEW.md)。
