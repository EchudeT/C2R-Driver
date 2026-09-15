# Driver Port Factory

Driver Port Factory（DPF）把 C 驱动跨平台迁移、公开验证、候选物封存和独立盲测组织成可审计的程序工作流。它严格区分确定性控制代码与 Codex 判断任务：程序拥有状态、门禁、哈希和执行结果，Codex 只提交有类型的分析或补丁产物。

本项目的规范来源是 `C-kernel-to-Rust` 的三个 Skill：

- `open-kernel-driver-port`：身份、采集、环境恢复和知识库；
- `knowledge-guided-driver-port`：目标研究、结构化翻译、测试迁移和 QEMU 验证；
- `blind-c2rust-driver-evaluation`：角色隔离、封存和私有评测。

DPF 允许把 Skill 原文及其 references 直接组合进阶段 Prompt。每次组合都会记录文档 SHA256，并把完整 Prompt 放入内容寻址存储。

## 当前可运行能力

- 按角色与评测模式生成阶段 DAG；
- clone 前的请求解析、候选驱动发现、单次确认和迁移范围冻结；
- source/target/QEMU 的轻量 revision 解析、任务本地 bare fetch 和受控 worktree；
- 主机/目标/QEMU 路线发现、不可覆盖恢复尝试和真实 `EXPERIMENT_READY` 门禁；
- provenance-checked 本地知识库、目标专项 probes 和项目 KB Skill 生成；
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
  --skill-root /path/to/C-kernel-to-Rust/skill

python -m driver_port_factory.cli status ./runs/ne2000
python -m driver_port_factory.cli intake analyze ./runs/ne2000 \
  --request "把 Linux 的 NE2000 PCI 驱动迁移到星绽OS" \
  --catalog examples/fixtures/linux-ne2000.catalog.json
python -m driver_port_factory.cli intake show ./runs/ne2000
python -m driver_port_factory.cli acquire plan ./runs/ne2000
python -m driver_port_factory.cli acquire run ./runs/ne2000
python -m driver_port_factory.cli acquire verify ./runs/ne2000
python -m driver_port_factory.cli environment inspect ./runs/ne2000
```

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

详细设计见 [Skill 规范追踪矩阵](docs/SKILL_TRACEABILITY.md)、[架构](docs/ARCHITECTURE.md)、[迁移需求门](docs/INTAKE.md)、[Git acquisition](docs/ACQUISITION.md)、[环境恢复](docs/ENVIRONMENT_RECOVERY.md)、[知识库](docs/KNOWLEDGE_BASE.md)、[阶段工作流](docs/WORKFLOW.md)、[实施计划](docs/IMPLEMENTATION_PLAN.md) 和 [Codex 任务契约](docs/CODEX_JOBS.md)。
