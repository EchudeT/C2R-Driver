# Driver Port Factory

Driver Port Factory（DPF）把 Linux C 驱动迁移到 Asterinas Rust 驱动的过程组织成可恢复、可审计的 workspace。控制器保存阶段状态、哈希链、Codex 调用计量、源代码来源、构建产物和 QEMU 运行证据；失败 attempt 保留为失败，不会被后续文字改写成成功。

仓库内已经包含上游 `C-kernel-to-Rust` Skill 的工作副本：

- [`skill/open-kernel-driver-port/SKILL.md`](skill/open-kernel-driver-port/SKILL.md)：输入确认、原始仓库获取、环境恢复和知识库；
- [`skill/knowledge-guided-driver-port/SKILL.md`](skill/knowledge-guided-driver-port/SKILL.md)：目标研究、源码理解、契约、实现、测试迁移和 QEMU 验证；
- [`skill/linux-asterinas-driver-map/SKILL.md`](skill/linux-asterinas-driver-map/SKILL.md)：可选的 Linux/Asterinas 原内核路径地图；
- [`skill/blind-c2rust-driver-evaluation/SKILL.md`](skill/blind-c2rust-driver-evaluation/SKILL.md)：盲测相关协议。

Skill 是这个仓库的一部分，不需要再给 `port run` 传 `--skill-root`。只有复现实验中另一个明确冻结的 Skill 树时才使用 `--skill-root` 或 `DPF_SKILL_ROOT`。

`skill/` 是从 `/home/unix/file/C2R-Driver/C-kernel-to-Rust/skill` 按原文件复制的本地副本，不包含上游仓库的 `.git` 元数据；对 Skill 的修改应在本仓库中审查和提交。

## 目录结构

```text
driver-port-factory/
├── src/driver_port_factory/       控制器、阶段服务、验证器和 CLI
├── skill/                         原版 C-kernel-to-Rust Skill 副本
├── configs/drivers/               共享驱动候选 catalog（输入，不是结果）
├── scripts/                       启动、续跑、监控、计费和诊断入口
├── docs/                          阶段、证据、审计和故障处理说明
├── examples/fixtures/             测试 fixture
└── tests/                         单元测试和合成控制器测试
```

每个实验使用单独的 workspace，例如 `runs/e1000` 或 `/tmp/e2e-e1000-01`。workspace 的 `.dpf/` 是控制面：`project.json` 保存冻结请求，`run.sqlite3` 保存事件账本，CAS 保存不可变 artifact，`codex/` 保存调用 sidecar、事件和结果。不要把实验 workspace 复制回 `configs/`，也不要手工编辑 `.dpf/` 状态。

## 环境准备

DPF 要求 Python 3.11 或更高版本。系统 `python` 可能仍是 Python 3.8，因此使用仓库虚拟环境：

```sh
cd /home/unix/file/C2R-Driver/c2rust-migration-test-01/driver-port-factory
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e '.[dev,codex]'
```

如果 `.venv/bin/pip` 不存在，说明虚拟环境是不完整的；用本机的 Python 3.11 重新创建它（不要把系统 Python 3.8 混进来）：

```sh
python3.11 -m venv --clear --upgrade-deps .venv
```

然后再次执行上面的安装命令。

还需要 Git、Codex CLI、`strace`、Docker、与冻结证据匹配的 QEMU（通常为 `qemu-system-x86_64`）以及 Asterinas 官方 dev image。Codex provider、模型和 `CODEX_HOME` 也必须已配置。源仓库、目标仓库、QEMU、detached worktree、CAS 和 QEMU 镜像需要足够磁盘空间。

```sh
.venv/bin/python --version       # 必须 >= 3.11
codex --version
git --version
docker --version
strace --version
qemu-system-x86_64 --version
```

Codex 模型可用 `--model` 指定，也可在 `CODEX_HOME` 中配置默认值。DPF 费用根据本地 metrics 和公开价格表估算，不是 relay 或账单发票；没有完整 usage 的调用必须显示为 `unknown`。

如果已有原始仓库，优先传入本地 checkout 或 bare repository，避免重复下载并让采集严格离线：

```text
--local-source-repository /absolute/path/to/linux
--local-target-repository /absolute/path/to/asterinas
--local-qemu-repository /absolute/path/to/qemu
```

这些路径按角色冻结到 workspace。控制器只导入所选 commit；找不到 commit 时失败，不会悄悄回到网络。详见 [`docs/ACQUISITION.md`](docs/ACQUISITION.md)。

## 启动一个实验

推荐使用封装脚本。它使用仓库内 Skill、保持 controller 在前台，并清除可能由旧受限 Codex 运行遗留的 `CODEX_SANDBOX_NETWORK_DISABLED`：

```sh
./scripts/run-experiment.sh \
  --workspace ./runs/e1000 \
  --source-platform linux \
  --target-platform asterinas \
  --driver-name e1000 \
  --catalog configs/drivers/linux-e1000.catalog.json \
  --model gpt-5.6-sol \
  --local-source-repository /absolute/path/to/linux \
  --local-target-repository /absolute/path/to/asterinas \
  --local-qemu-repository /absolute/path/to/qemu
```

`--catalog` 可以重复传入。共享候选目录及其用途见 [`configs/README.md`](configs/README.md)：

```text
configs/drivers/linux-ne2000.catalog.json
configs/drivers/linux-e1000.catalog.json
configs/drivers/linux-pvpanic-pci.catalog.json
```

默认开启分析审查和最终证据审查。此次开发实验明确不需要它们时，可以传：

```sh
--no-analysis-review --no-final-evidence-review
```

审查设置会冻结到 workspace，不能在 workspace 创建后随意改成另一种配置。脚本的完整参数见 `./scripts/run-experiment.sh --help`。

也可以直接调用 CLI；脚本只是减少重复参数和路径错误：

```sh
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli port run ./runs/e1000 \
  --source-platform linux --target-platform asterinas --driver-name e1000 \
  --catalog configs/drivers/linux-e1000.catalog.json
```

不要同时对同一个 workspace 启动两个 controller。`.dpf/controller.lock` 是保护边界；第二个 controller 会被拒绝，但两个不同 workspace 可以并行运行。`run-experiment.sh` 不自动 `&`、不自动创建隐藏 controller。若确实需要离开终端，请由用户显式使用 tmux、systemd 或作业调度器，并保存 PID/日志。

## 阶段和构建路径

一次默认开发运行最多有 18 个阶段；关闭两个可选审查后是 16 个阶段：

| 阶段 | 作用 |
| --- | --- |
| `project_init` | 创建 workspace、角色、模式、Skill 和审查策略的初始记录。 |
| `request_intake` | 解析平台、驱动、设备范围和用户约束。 |
| `driver_candidate_resolution` | 从 catalog 和源平台证据找到候选驱动。 |
| `scope_confirmation` | 在候选有歧义时要求一次确认，并冻结 bus、设备 ID、QEMU model 等范围。 |
| `migration_envelope_freeze` | 冻结允许修改的仓库、版本、证据边界和交付目标。 |
| `repository_acquisition` | 获取或导入 Linux、Asterinas、QEMU 的精确 commit 和来源记录。 |
| `evidence_closure` | 关闭源代码、目标 API、设备协议和环境证据缺口。 |
| `environment_recovery` | 验证主机、官方容器、QEMU 和可运行路线；不把启动成功当成驱动成功。 |
| `knowledge_base` | 建立带 provenance 的本地知识库和查询契约。 |
| `target_platform_study` | 研究设备注册、I/O、DMA、IRQ、网络/块设备和构建入口。 |
| `migration_handoff` | 将研究、证据和范围交给实现阶段。 |
| `migration_contracts` | 固化源码行为、迁移设计、测试计划和验收契约。 |
| `analysis_review`（可选） | 独立 AI 按核心代码位置、行号和证据审查分析材料。 |
| `target_framework_enablement` | 补齐目标框架最小 integration wiring，并验证编译/注册前置。 |
| `driver_implementation` | 工作者实现 Rust 驱动、运行自检、处理反馈和记录每次修复。 |
| `artifact_preparation` | 生成可运行制品、manifest、身份和 presence receipt；这里发生目标工程构建。 |
| `public_qemu_validation` | 执行公开 QEMU 阶梯，保存命令、设备身份、日志、退出码和归因。 |
| `final_evidence_review`（可选） | 独立 AI 一次性审查交付证据、代码定位和原始运行结果。 |

目标框架能力在 `target_framework_enablement` 准备，驱动实现之后由 `artifact_preparation` 准备制品并固定身份，`public_qemu_validation` 在 QEMU 中执行公开测试。实现自检在调用 QEMU 前执行通用检查：高置信度 marker 制品会被拦截；脚本里找不到 QMP 继续命令或 runtime 变量仅产生 advisory，由实际执行证据判定，避免误拒绝 helper／外部控制器。失败记录包含精确位置与实际观察。三个不同提交在输入和观察均相同时暂停续调，计数跨重启保存，同一提交重放不重复计数；helper 修复或新观察允许继续。可恢复修复只在当前允许的阶段进行，已封存的大阶段不会被静默回退。详见 [`docs/STAGE_GUIDE.md`](docs/STAGE_GUIDE.md)、[`docs/WORKFLOW.md`](docs/WORKFLOW.md) 和 [`docs/EXECUTION_RECOVERY.md`](docs/EXECUTION_RECOVERY.md)。

`status` 分开展示执行结果和功能评估来源，机械执行 PASS 不代表所有设备行为已覆盖。默认继续复用会话；如果需要摆脱过期诊断，可以在控制器停止后使用 `dpf codex reset-session RUN STAGE --reason "具体原因"`，为当前 provider/model 的 worker 或 reviewer 会话建立新上下文。旧会话归档，冻结证据和未完成状态通过 CAS 交接，代码和阶段状态保持原样，不调用模型。详见 [成本与质量优化说明](docs/COST_QUALITY_OPTIMIZATION_2026-09-26.zh-CN.md)。

## 查看进度和计费

查看完整阶段表、依赖、attempt、controller 状态和每阶段成本：

```sh
./scripts/status.sh ./runs/e1000
```

每 5 秒刷新：

```sh
./scripts/watch.sh ./runs/e1000 --interval 5
```

只看成本和 token：

```sh
./scripts/costs.py ./runs/e1000
./scripts/watch.sh ./runs/e1000 --costs --interval 5
```

字段含义：`input` 是输入 token，`cached` 是其中标记为缓存的输入，`output` 是输出 token；`unknown(n)` 表示 n 次调用没有可验证的完整 usage，`unpriced(n)` 表示有 usage 但没有对应价格模型或价格条件。它们不能当成 0。`USD~` 是根据本地记录、模型和公开价格的估算。

需要机器可读数据时：

```sh
./scripts/status.sh ./runs/e1000 --json > /tmp/e1000-status.json
```

查看某阶段的 Codex 事件和结果：

```sh
./scripts/transcript.sh ./runs/e1000 driver_implementation
./scripts/transcript.sh ./runs/e1000 driver_implementation --include-content
```

主要持久化位置：

```text
.dpf/controller.json              controller 是否运行、停止和区间
.dpf/run.sqlite3                  阶段状态、事件和哈希链账本
.dpf/codex/*.metrics.json         每个 Codex 调用的 token、模型和时间
.dpf/codex/*.events.jsonl         Codex 原始事件流
.dpf/codex/*.result               Codex 结果文本
```

脚本入口的职责固定如下：

| 脚本 | 用途 |
| --- | --- |
| `run-experiment.sh` | 创建或恢复一个前台实验 controller。 |
| `resume-experiment.sh` | 从 `.dpf/project.json` 读取请求并续跑现有 workspace。 |
| `status.sh` | 输出完整阶段表，支持 `--json` 和价格参数。 |
| `costs.py` | 输出紧凑的逐阶段 token、模型和费用估算表。 |
| `watch.sh` | 用 `watch` 周期刷新状态或费用。 |
| `transcript.sh` | 输出某阶段的 Codex prompt、结果和事件 artifact 索引。 |
| `processes.sh` | 只读列出 controller、Codex、Docker 和 QEMU 进程。 |
| `reopen-stage.sh` | 记录原因并释放一个明确的 blocked 阶段。 |
| `rerun-stage.sh` | 释放阶段后立即调用续跑脚本。 |

## 续跑、重跑和失败处理

控制器停止后，用同一 workspace 续跑；已经通过的阶段不会重跑：

```sh
./scripts/resume-experiment.sh --workspace ./runs/e1000
```

如果阶段 3 尚未冻结候选，再补上原 catalog：

```sh
./scripts/resume-experiment.sh \
  --workspace ./runs/e1000 \
  --catalog configs/drivers/linux-e1000.catalog.json
```

`resume-experiment.sh` 从 `.dpf/project.json` 读取 source、target 和 driver，避免手工输入不一致。模型、Codex 可执行文件和 catalog 是可选覆盖；不要在已有 workspace 上重新传不同的本地仓库或审查开关。需要改变这些输入时创建新的 workspace。

对于明确的 `BLOCKED` 阶段，先记录原因、释放阶段，再恢复：

```sh
./scripts/rerun-stage.sh \
  ./runs/e1000 driver_implementation \
  '修复目标框架挂载缺失后重试实现自检' \
  --model gpt-5.6-sol
```

只想释放阶段而暂不启动 Codex：

```sh
./scripts/reopen-stage.sh ./runs/e1000 driver_implementation '补齐本地 QEMU 挂载证据'
```

释放前确认 controller 已停止，并阅读 status 中的 `repair`、`recovery` 和失败 artifact。不要直接修改 SQLite、`project.json` 或状态枚举；状态变化应由 CLI/脚本写入账本。

## 故障定位

先运行：

```sh
./scripts/status.sh ./runs/e1000
./scripts/processes.sh
```

按问题查看这些文件：

| 现象 | 首先查看 |
| --- | --- |
| 阶段停在 `READY/RUNNING/BLOCKED` | `.dpf/controller.json`、status 输出、`.dpf/run.sqlite3` 最新事件 |
| Codex 没返回或费用不完整 | `.dpf/codex/*.metrics.json`、对应 `.events.jsonl`、`.result`；`unknown_usage` 不能当作零 |
| checker/recovery 有争议 | `.dpf/checker-decisions/`、submission artifact、`scripts/transcript.sh` |
| 源/目标/QEMU 版本不对 | acquisition manifest、`.dpf` CAS artifact、[`docs/ACQUISITION.md`](docs/ACQUISITION.md) |
| 目标工程编译失败 | `target_framework_enablement` 或 `artifact_preparation` 的报告、目标 worktree、`implementation-smoke/*/receipt.json` |
| QEMU 启动或设备观察失败 | `.dpf/public-qemu/*`、attempt receipt、原始 stdout/stderr、`execve.log` |
| Docker 执行失败 | `container-processes.json`、`container_execution.observation_errors`；若出现 bind mount 缺失，检查 workspace 和仓库是否按精确路径挂载 |
| 内存或磁盘异常 | `scripts/processes.sh` 的 Codex/Docker/QEMU 进程、workspace 大小和 `.dpf/codex` 事件文件 |

容器路线必须使用与证据匹配的 Asterinas 官方 dev image。容器能启动、内核能打印日志或 QEMU 能打开窗口，都不足以证明驱动工作；必须看到目标驱动进入制品、设备身份匹配、测试 oracle 通过，并且 receipt、退出码和归因完整。

如果要审计账本完整性：

```sh
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli ledger verify ./runs/e1000
```

如果怀疑有残留后台进程，不要通过重复启动来“试探”。先运行 `scripts/processes.sh`，确认对应 workspace 的 controller、Codex、Docker 和 QEMU，再结束明确属于该 workspace 的进程；其他实验的进程不要处理。

## 开发和验证 DPF 本身

源码编辑后：

```sh
.venv/bin/python -m compileall -q src
bash -n scripts/*.sh
.venv/bin/python -m pytest -q
```

完整测试依赖 `.[dev]`。测试不能替代真实 Linux/Asterinas/QEMU 证据；合成 controller fixture 只验证流程和契约。阶段协议、artifact 类型、角色门禁和 Skill 对齐见 [`docs/SKILL_TRACEABILITY.md`](docs/SKILL_TRACEABILITY.md)、[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)、[`docs/CODEX_JOBS.md`](docs/CODEX_JOBS.md) 和 [`docs/WORKFLOW_ALIGNMENT.md`](docs/WORKFLOW_ALIGNMENT.md)。

新建 developer-evidence 项目默认 `analysis-handoff`：分析材料、契约及已启用的分析审查完成后，在首次框架使能前交接一次；框架使能、驱动实现及连续修复复用新会话。已有项目保留其设置，未记录策略的旧项目仍为 `persistent`。可用 `--context-policy persistent` 关闭交接，或选 `implementation-handoff` 将交接推迟至首次驱动实现前。用 `dpf codex context-report RUN [--compare OTHER_RUN] [--stage STAGE]` 查看费用、缓存、会话连续性与证据，不调用模型，也不把执行 PASS 当成质量等价。详见 [分析到执行的上下文交接](docs/ANALYSIS_HANDOFF_POLICY.zh-CN.md)。

提示现支持阶段阅读导航、修复观测差异及长反馈按需读取；没有新信息时省略空摘要。高风险接口探针在现有节点内按需选择，不增加固定验收清单。实现范围、成本边界和只读回放工具见 [阅读导航与变化驱动修复](docs/CONTEXT_FOCUS_AND_PROBES.zh-CN.md)。

交接包现在携带同一修复周期的最近两条 controller observation 及变化，帮助核对过期归因；`context-report` 增加保持 token 不变的缓存成本敏感性统计。两者均不触发新门禁或付费调用。证据、边界和下一步实验设计见 [后续优化研究](docs/WORKFLOW_OPTIMIZATION_FOLLOWUP_2026-09-26.zh-CN.md)。

第二轮限额探针发现当前中转在请求输出上限 64 时实际返回 403 token，因此已停止后续付费实验；信息更对等的四组实验材料已冻结为 dry-run。执行器限额诊断与列表证据变化跟踪已改进，见 [第二轮实验记录](docs/CONTEXT_EXPERIMENT_ROUND2_2026-09-26.zh-CN.md)。

用户随后授权适量实验，已用显式观察用量策略完成四组共 8 次调用：补充短机制证据比单纯删历史更有诊断价值，默认仍复用会话。本轮同时保留重复快照之前的关键观察转变，并统一 preflight advisory 提示。数据与适用边界见 [四组实验结果](docs/CONTEXT_FACTORIAL_RESULTS_2026-09-26.zh-CN.md)。
