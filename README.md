# Driver Port Factory

Driver Port Factory（DPF）把 C 驱动跨平台迁移、公开验证、候选物封存和独立盲测组织成可审计的
程序工作流。它严格区分确定性控制代码与 Codex 判断任务：程序拥有状态、门禁、哈希和执行结果；
Codex Prompt、原始响应和事件只作辅助证据，required artifact 由明确的领域 adapter 生成和验证。

本项目的规范来源是 `C-kernel-to-Rust` 的三个 Skill：

- `open-kernel-driver-port`：身份、采集、环境恢复和知识库；
- `knowledge-guided-driver-port`：目标研究、结构化翻译、测试迁移和 QEMU 验证；
- `blind-c2rust-driver-evaluation`：角色隔离、封存和私有评测。

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
- 固定 C 编译配置、编译器依赖扫描、七类源码闭包与知识库增量重建；
- 从冻结 argv 导出 Clang AST/CFG/layout/preprocessor、LLVM IR 和 AST-derived CPG；
- SQLite 保存阶段状态和哈希链事件；
- SHA256 内容寻址产物库；
- 阶段依赖、必需输出和角色门禁；
- Skill Prompt 选择、组合和快照；
- `codex exec` 结构化调用及 Python SDK Gateway；
- 来源平台、目标平台、设备类别与 QEMU 插件协议；
- 候选物 canonical manifest 和封存摘要；
- 开发、前瞻盲测、事后封存盲测三种模式的时序骨架。

## 快速开始

```sh
python -m driver_port_factory.cli init ./runs/ne2000 \
  --source linux --target asterinas --driver ne2k-pci \
  --mode developer-evidence --role developer \
  --skill-root /path/to/C-kernel-to-Rust/skill \
  --prompt-pack /path/to/editable/prompt-pack

python -m driver_port_factory.cli status ./runs/ne2000
python -m driver_port_factory.cli intake analyze ./runs/ne2000 \
  --request "把 Linux 的 NE2000 PCI 驱动迁移到星绽OS" \
  --catalog examples/fixtures/linux-ne2000.catalog.json
python -m driver_port_factory.cli intake show ./runs/ne2000
python -m driver_port_factory.cli codex run ./runs/ne2000 revision_selection \
  --objective "Select maintained, mutually compatible source, target, and QEMU releases"
python -m driver_port_factory.cli acquire revision-proposal-import ./runs/ne2000 \
  --job-digest SHA256 --job-ordinal N
python -m driver_port_factory.cli acquire revisions ./runs/ne2000 \
  --proposal-digest SHA256 --proposal-ordinal N
python -m driver_port_factory.cli acquire repositories ./runs/ne2000
python -m driver_port_factory.cli codex run ./runs/ne2000 evidence_closure \
  --objective "Locate the minimum evidence closure across the six evidence domains"
python -m driver_port_factory.cli acquire proposal-import ./runs/ne2000 \
  --job-digest SHA256 --job-ordinal N
python -m driver_port_factory.cli acquire closure-finalize ./runs/ne2000 \
  --proposal-digest SHA256 --proposal-ordinal N
python -m driver_port_factory.cli acquire repositories-verify ./runs/ne2000
python -m driver_port_factory.cli environment inspect ./runs/ne2000
```

`--prompt-pack` 可省略以使用随包提供的默认 Pack，也可在每次 `dpf prompt render` 或
`dpf codex run` 时覆盖。修改 Pack 或 Skill 后无需改代码、重建数据库或迁移旧摘要；已运行 Job
仍由 CAS 中的完整 Prompt 和 SHA256 复现。只有正式 held-out batch 才在实验层冻结所选版本。

这里的 NE2000 catalog 只是集成测试 fixture，不会安装进生产包。正式运行由源平台 Resolver 加载一个或多个带来源、版本和 SHA256 的轻量元数据 provider。如果输入只有 `NE2000`，fixture 会产生 PCI、ISA、PCMCIA 候选的合并问题并进入 `WAITING_FOR_USER`。随后执行：

```sh
python -m driver_port_factory.cli intake answer ./runs/ne2000 \
  --candidate-id linux-ne2k-pci \
  --answer "选择 PCI ne2k-pci，排除 ISA 和 PCMCIA"
```

在源码仓库中直接运行时：

```sh
PYTHONPATH=src python -m driver_port_factory.cli --help
python -m unittest discover -s tests -v
```

详细设计见 [Skill 规范追踪矩阵](docs/SKILL_TRACEABILITY.md)、[架构](docs/ARCHITECTURE.md)、[迁移需求门](docs/INTAKE.md)、[Git acquisition](docs/ACQUISITION.md)、[环境恢复](docs/ENVIRONMENT_RECOVERY.md)、[知识库](docs/KNOWLEDGE_BASE.md)、[目标平台研究](docs/TARGET_PLATFORM_STUDY.md)、[C 源码闭包](docs/SOURCE_CLOSURE.md)、[结构化 C 分析](docs/STRUCTURED_C_ANALYSIS.md)、[阶段工作流](docs/WORKFLOW.md)、[实施计划](docs/IMPLEMENTATION_PLAN.md) 和 [Codex 任务契约](docs/CODEX_JOBS.md)。
