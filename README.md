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

每个实验使用单独的 workspace，例如 `runs/e1000` 或 `/tmp/e2e-e1000-01`。workspace 的 `.dpf/` 是控制面：`project.json` 保存冻结请求，`run.sqlite3` 保存事件账本，CAS 保存不可变 artifact，`codex/` 保存调用 sidecar、事件和结果。不要把实验 workspace 复制回 `configs/`，也不要手工编辑 `.dpf/` 状态。新任务使用 `work/target` 等简短目录，模型读取证据使用 `.dpf/e/E15` 等短入口；完整版本与哈希保留在控制器记录中。详见 [短引用与路径](docs/SHORT_REFERENCES.zh-CN.md)。

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

07 采集恢复已补充具体失败路径诊断、未完成采集时的提案提交约束和按工作者响应计数的重复失败停止规则；详见 [证据采集恢复](docs/EVIDENCE_RECOVERY.zh-CN.md)。离线控制器回归与真实驱动实验结果分开记录。

可通过 `--max-model-cost-usd 10` 配置每次模型调用前的累计估算费用检查；未知费用会停止续调，单次调用仍可能超出剩余额度。

统一逐功能实现已加入[局部适配与成本优化](docs/LOCAL_ADAPTATION_COST.zh-CN.md)：目标平台设计前提的修正留在当前功能和会话，短进度由 `driver_checks.progress` 交回，避免级联重置。冻结合同/环境修订与最终验收仍保留；离线回归和真实降本效果分开记录。

实现长回合的真实成本已完成[专项审计与优化](docs/IMPLEMENTATION_COST.zh-CN.md)：补齐模型实际收到的平台接口、提供固定容器内格式化、按行为提供提示与精简检查反馈，并取消固定七主题检索配额。离线回归 377 项通过；实验 05 已重新启动，真实降本效果待完整结果；实验 04 保持停止。

新开发项目已加入[分析边界重构](docs/ANALYSIS_BOUNDARY.zh-CN.md)：分析会话与采集/环境分离，
显式交付当前请求、源码入口和功能范围；`--behavior-scope` 可冻结行为子集与接入层级。
前置分析只解决源义务和关键适配前提，局部实现细节留在当前行为。08 提供合并 bootstrap 和简短反馈。
真实降本效果尚未验证；实验 06 在累计约 $11.66 时因 $10 调用间预算停止，尚未完成驱动交付。

新任务的[联合分析提交格式](docs/ROUTE_GUIDED_ANALYSIS.zh-CN.md#提交格式与同轮修正)已简化：
正文用短 ID 定位，普通源码前提无需探测字段，空检索索引可省略；提交时合并反馈可检查的错误，
允许同一回合修正重交，避免模型结束后才发现格式错误。控制器仍验证真实来源与收据。
这是接口改进，尚未用 luna 实验验证费用与成功率；运行中的冻结实验保持原版本。

最后工作包现已支持[直接交付与统一验收](docs/FINAL_PACKAGE_HANDOFF.zh-CN.md)：
预置case集合决定必需运行检查，分析不能自行追加ktest等必交任务；最后一次done在当前回合
交回简短自查说明，不再增加done门禁或固定追加报告模型回合。14/15保留独立控制器
验收。实现阶段复用历史通过结果；15针对同一最终代码快照和产物完整运行预置测试一次，
全部通过即结束。临时目录、PATH、日志和报告变化不触发另一轮；不拼接不同版本的单项PASS。实验09已使用原有结果协助完成15；新策略的真实降本效果尚未验证。

## 平台执行层

镜像与加速器必须由操作者在新任务启动时通过 `--platform-image`、`--platform-accelerator`
指定并保存在项目配置中。模型仅调用 `bootstrap`，不选择环境；构建和 QEMU 在 Docker 内执行，
OVMF 使用固定容器路径，KVM 通过 `/dev/kvm` 传入。宿主显示 WSL2 不意味着使用 TCG。
详见[配置传递修复](docs/CONFIGURED_PLATFORM_ROUTE.zh-CN.md)。本轮按用户要求未运行测试或重启实验。

新的 Linux pvpanic-pci → Asterinas 全范围任务已提供[固定公开测试](docs/PREPARED_PUBLIC_TESTS.zh-CN.md)：
控制器预装真实 panic、关机和控制/移除三个场景的刺激、断言及运行入口。模型只实现驱动、适配
测试接口并运行检查，不能改写断言。单设备 QEMU 覆盖不代表全部语义正确，真实新实验结果待运行。
其他驱动仍使用[通用测试入口与登记工具](docs/PREPARED_TEST_ENTRYPOINTS.zh-CN.md)，尚无预置设备断言。

新任务已加入[逐行为工作包与工具优化](docs/BEHAVIOR_COST_TOOLS.zh-CN.md)：简单驱动可用一个完整工作包覆盖多个契约与测试、
同一正文只传一次、按需生成 Asterinas 组件接入骨架，运行用例支持内联对象和 QMP 返回值/事件字段断言。
当前行为先完成必要检查再推进，最终验收保留。历史 B2 的离线重放中上下文 JSON 减少约 14.4%；
这不是费用降幅，真实模型采用情况与成本收益尚待新实验验证。

新任务支持[启动日志断言与原记录重判](docs/BOOT_LOG_CHECKS.zh-CN.md)：控制器处理颜色符、
排除命令回显，失败时直接返回相关 guest 输出；只改正向日志断言时可以检查已存运行记录，
原失败仍保留。当前行为完成前的必要检查不推迟，最终验收不减项。真实降本尚待新实验验证。

开发版本已将平台长命令统一到等待终态的 `driver_checks.platform`，监视器直接显示执行日志；
构建前先解析依赖并记录锁文件变化，再冻结输入。新增按需组件接入源码导航，以及防止
串口命令回显误通过的 `guest_assert`。见[平台交互重构](docs/PLATFORM_WORKER_EXECUTION.zh-CN.md)。
真实降本尚未测量；本地执行器验证与完整模型翻译实验分开报告，冻结实验不热更新。

新建 Asterinas x86_64 项目现在由控制器提供固定构建、镜像与缓存挂载、ISO 身份、QMP/串口、超时和清理工具。第 08 阶段须完成干净目标基线的实际构建与启动验证；模型负责驱动适配和设备特定测试。已实测官方镜像下 KVM 路线；其他平台/启动方式未通用化，TCG 未声称通过。本开发分支不兼容旧冻结任务；旧记录保留供原版本审计。实际 CLI 接入曾出现项目校验模式错误，已修复并补充命令入口、失败恢复及损坏证据拒绝回归；运行中的冻结实验不热更新。详见 [平台执行层与实测记录](docs/PLATFORM_EXECUTION.zh-CN.md)。

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
  --platform-image asterinas/dev:0.18.1-20260805-dpf-ovmf-bar-v1 \
  --platform-accelerator kvm \
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

迁移质量主体是工作模型自检和实际验收测例。分析审查、最终证据审查是可选辅助，当前默认关闭。显式开启时传：

```sh
--analysis-review --final-evidence-review
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

默认开发运行有 15 个账本阶段；两个模型审查默认关闭，显式全部开启时为 17 个阶段（配置 benchmark 另加一个静态验收阶段）。这不是模型调用次数：分析合并交付，框架/实现/包装连续工作，控制器复用已有收据。

| 阶段 | 作用 |
| --- | --- |
| `project_init` | 创建 workspace、角色、模式、Skill 和审查策略的初始记录。 |
| `request_intake` | 解析平台、驱动、设备范围和用户约束。 |
| `driver_candidate_resolution` | 从 catalog 和源平台证据找到候选驱动。 |
| `scope_confirmation` | 在候选有歧义时要求一次确认，并冻结 bus、设备 ID、QEMU model 等范围。 |
| `migration_envelope_freeze` | 冻结允许修改的仓库、版本、证据边界和交付目标。 |
| `repository_acquisition` | 获取或导入 Linux、Asterinas、QEMU 的精确 commit 和来源记录。 |
| `evidence_closure` | 准备初始相关原始材料与明确缺口，后续按问题读取固定仓库。 |
| `environment_recovery` | 托管执行器验证基线构建和启动，直接绑定收据，不额外要求设备探针。 |
| `knowledge_base` | 建立带 provenance 的本地知识库和查询契约。 |
| `target_platform_study` | 一次联合分析形成源义务、粗路径、关键前提、必要探测、契约和行为计划。 |
| `migration_handoff` | 将研究、证据和范围交给实现阶段。 |
| `migration_contracts` | 控制器登记同一份联合分析，不再单独调用模型生成契约。 |
| `analysis_review`（可选） | 独立 AI 按核心代码位置、行号和证据审查分析材料。 |
| `driver_implementation` | 默认每轮一个行为，随需补齐框架、驱动接入及对应测试；最终统一提交源码证据。 |
| `artifact_preparation` | 生成可运行制品、manifest、身份和 presence receipt；这里发生目标工程构建。 |
| `public_qemu_validation` | 执行公开 QEMU 阶梯，保存命令、设备身份、日志、退出码和归因。 |
| `final_evidence_review`（可选） | 独立 AI 一次性审查交付证据、代码定位和原始运行结果。 |

新开发任务默认逐行为调度，框架适配在需要它的行为中完成；统一在 `driver_implementation` 验收。本次路线协议不兼容旧冻结工作区，请创建新任务；旧实验记录不改写。详见 [合并实现与去重规则](docs/UNIFIED_IMPLEMENTATION.zh-CN.md)。实现之后由 `artifact_preparation` 准备制品并固定身份，`public_qemu_validation` 在 QEMU 中执行公开测试。实现自检在调用 QEMU 前执行通用检查：高置信度 marker 制品会被拦截；脚本里找不到 QMP 继续命令或 runtime 变量仅产生 advisory，由实际执行证据判定，避免误拒绝 helper／外部控制器。失败记录包含精确位置与实际观察。三个不同提交在输入和观察均相同时暂停续调，计数跨重启保存，同一提交重放不重复计数；helper 修复或新观察允许继续。开发模式可因具体反证修订早期证据或设计，按数据依赖失效受影响结果并保留源码与失败记录；封存模式仍遵循其冻结边界。详见 [`docs/STAGE_GUIDE.md`](docs/STAGE_GUIDE.md)、[`docs/WORKFLOW.md`](docs/WORKFLOW.md) 和 [`docs/EXECUTION_RECOVERY.md`](docs/EXECUTION_RECOVERY.md)。

`status` 分开展示执行结果和功能评估来源，机械执行 PASS 不代表所有设备行为已覆盖。默认继续复用会话；如果需要摆脱过期诊断，可以在控制器停止后使用 `dpf codex reset-session RUN STAGE --reason "具体原因"`，为当前 provider/model 的 worker 或 reviewer 会话建立新上下文。旧会话归档，冻结证据和未完成状态通过 CAS 交接，代码和阶段状态保持原样，不调用模型。详见 [成本与质量优化说明](docs/COST_QUALITY_OPTIMIZATION_2026-09-26.zh-CN.md)。

## 查看进度和计费

查看完整阶段表、依赖、attempt、controller 状态和每阶段成本：

```sh
./scripts/status.sh ./runs/e1000
```

默认使用左右分栏：左侧阶段状态和当前调用成本，右侧模型消息、命令及输出。每 5 秒刷新：

```sh
./scripts/watch.sh ./runs/e1000 --interval 5
```

方向键选择阶段，`f` 恢复跟随最新调用，`PgUp/PgDn` 翻阅输出，`End` 回到末尾，`q` 退出。
`--classic` 保留原完整状态视图，`--once` 输出一次分栏快照（可重定向）。监视器只读，不启动模型或续跑。

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
| 目标工程编译失败 | `driver_implementation`（旧任务还包括 `target_framework_enablement`）或 `artifact_preparation` 的报告、目标 worktree、`implementation-smoke/*/receipt.json` |
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

后续工作流改进建议见 [借鉴 Driver Port Lab：减少契约遗漏与实现返工](docs/DRIVER_PORT_LAB_LESSONS_2026-10-02.zh-CN.md)。实现接口见 [benchmark 与开发工具](docs/BENCHMARK_AND_DELIVERY_TOOLS.zh-CN.md)：支持配置冻结的 benchmark 程序验收、受控检查工具和新开发任务默认启用的行为调度。模型审查保持可选；benchmark 用例由使用方定义。

源码编辑后：

```sh
.venv/bin/python -m compileall -q src
bash -n scripts/*.sh
.venv/bin/python -m pytest -q
```

完整测试依赖 `.[dev]`。测试不能替代真实 Linux/Asterinas/QEMU 证据；合成 controller fixture 只验证流程和契约。阶段协议、artifact 类型、角色门禁和 Skill 对齐见 [`docs/SKILL_TRACEABILITY.md`](docs/SKILL_TRACEABILITY.md)、[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)、[`docs/CODEX_JOBS.md`](docs/CODEX_JOBS.md) 和 [`docs/WORKFLOW_ALIGNMENT.md`](docs/WORKFLOW_ALIGNMENT.md)。

新建 developer-evidence 项目默认 `analysis-handoff`：分析材料、契约及已启用的分析审查完成后，在首次实现前交接一次；逐行为实现、局部框架适配及连续修复复用新会话。新路线任务保存自己的会话设置。可用 `--context-policy persistent` 关闭交接，或选 `implementation-handoff` 将交接推迟至首次驱动实现前。用 `dpf codex context-report RUN [--compare OTHER_RUN] [--stage STAGE]` 查看费用、缓存、会话连续性与证据，不调用模型，也不把执行 PASS 当成质量等价。详见 [分析到执行的上下文交接](docs/ANALYSIS_HANDOFF_POLICY.zh-CN.md)。

提示现支持阶段阅读导航、修复观测差异及长反馈按需读取；没有新信息时省略空摘要。高风险接口探针在现有节点内按需选择，不增加固定验收清单。实现范围、成本边界和只读回放工具见 [阅读导航与变化驱动修复](docs/CONTEXT_FOCUS_AND_PROBES.zh-CN.md)。

每次模型调用默认保留精确提示和可用的原生会话快照，按内容分块去重，不增加模型请求。`dpf codex context-logs RUN` 查看覆盖与压缩记录；`dpf codex context-log-export RUN JOB_ID DESTINATION` 校验并还原日志。采集失败不会阻断翻译。详见 [日志说明与完整实验命令](docs/CONTEXT_LOGGING.zh-CN.md)。

交接包现在携带同一修复周期的最近两条 controller observation 及变化，帮助核对过期归因；`context-report` 增加保持 token 不变的缓存成本敏感性统计。两者均不触发新门禁或付费调用。证据、边界和下一步实验设计见 [后续优化研究](docs/WORKFLOW_OPTIMIZATION_FOLLOWUP_2026-09-26.zh-CN.md)。

第二轮限额探针发现当前中转在请求输出上限 64 时实际返回 403 token，因此已停止后续付费实验；信息更对等的四组实验材料已冻结为 dry-run。执行器限额诊断与列表证据变化跟踪已改进，见 [第二轮实验记录](docs/CONTEXT_EXPERIMENT_ROUND2_2026-09-26.zh-CN.md)。

用户随后授权适量实验，已用显式观察用量策略完成四组共 8 次调用：补充短机制证据比单纯删历史更有诊断价值，默认仍复用会话。本轮同时保留重复快照之前的关键观察转变，并统一 preflight advisory 提示。数据与适用边界见 [四组实验结果](docs/CONTEXT_FACTORIAL_RESULTS_2026-09-26.zh-CN.md)。

知识库现支持代码符号BM25与本地embedding混合RAG，输出有界、带原文行号的生成上下文。
通过`DPF_KB_EMBEDDING_MODEL`在controller建库时配置固定本地模型；工作者使用
`knowledge rag`或`tool_runtime.knowledge_rag`按需取证。索引过期不会静默降级。
配置、真实检索探针及能力边界见[RAG说明](docs/RAG.zh-CN.md)。

本轮已落实目标知识查询重放与原文绑定、工作者合规自检职责和封存审计归因修复。
具体边界及新 workspace 要求见 [Skill 对齐记录](docs/SKILL_ALIGNMENT_2026-10-02.zh-CN.md)。

指南要求的资料类型、机制与实际语料准备状态见 [知识库指南核对](docs/KNOWLEDGE_BASE_GUIDE_ALIGNMENT.zh-CN.md)。

跨驱动共享库现可积累固定原文、公开实验观察及有证据引用的经验。07 资料收集前即固定快照并提供初始检索结果，优先复用共享库导航和本地固定版本原文，缺失部分再由控制器获取；后续建库沿用同一快照。初始真实内容与使用方法见 [共享知识库](docs/SHARED_KNOWLEDGE.zh-CN.md)。

模型接口现支持工作区短引用、自动 job 绑定及保留查询条件的续读游标；完整哈希仍由控制器保存和校验。接口与适用边界见 [短引用说明](docs/SHORT_REFERENCES.zh-CN.md)。

07/08 现提供有界仓库/环境导航、可复用的 Docker smoke 入口和具体失败原因；设备断言与实际验收保留。已验证能力及同驱动重跑尚未闭合的研究复用边界见 [前置阶段优化](docs/BOOTSTRAP_OPTIMIZATION.zh-CN.md)。

环境执行已完成[采集重构与 OVMF 兼容修复](docs/OVMF_AND_EXECUTION_REFACTOR.zh-CN.md)：托管容器内跟踪替代短命进程轮询，基础设施故障不进入付费返修。OVMF 的 pvpanic BAR 兼容补丁作为显式本地派生镜像提供，原 QEMU 与目标内核不变。真实平台检查通过，端到端驱动成本仍待新实验。


## 路线驱动的联合分析

`feature/route-guided-v1` 将流程收敛为分析、连续实现、验收三个宏观阶段。
分析只交付一份 Markdown 和 `.route.json` 引用索引，初始行为直接交给调度器；
实现每轮一个完整行为。局部路线修改保留无关进度，普通源码修改不触发额外前提审查。
必要探测复用配置好的执行器，选择的分析经验可进入共享知识库，模型审查默认关闭。

当前接口已实现，完整离线回归 413 项通过；回归启动后的局部调整另经 25 项针对性测试通过。
核心模块 Ruff 检查及格式检查通过，全库仍有存量检查问题；详见
[本版验证记录](docs/audits/route-guided-2026-10-03/verification.zh-CN.md)。尚无本版真实模型端到端成本结果。
已有平台实测不能替代新工作流实验。规格、提示词位置和验证边界见
[路线分析](docs/ROUTE_GUIDED_ANALYSIS.zh-CN.md)与[阶段职责](docs/STAGE_GUIDE.md)。

共享经验新增三字段 `knowledge_learn` 工具，控制器归档后于公开验收通过时发布；
分析入口提供少量匹配经验，按需检索保留。隔离实验可配置 `DPF_SHARED_KB_PUBLISH`
写回公共库，`knowledge learning RUN` 查看记录。没有新增模型阶段或交付门禁；
真实跨驱动降本效果待验证，详见 [共享知识库](docs/SHARED_KNOWLEDGE.zh-CN.md)。

Linux evbug 已增加[固定公开实验接口](docs/EVBUG_PUBLIC_EXPERIMENT.zh-CN.md)：控制器提供事件、
生命周期与边界三个场景，以及输入子系统内的固定Rust断言；模型只实现驱动与调用接口。
公开场景使用合成设备，通过完整Asterinas内核执行，不代表实机或完整源语义覆盖。真实结果待运行。

NE2000 PCI 的[公开实验准备](docs/NE2K_PUBLIC_EXPERIMENT.zh-CN.md)已提供固定的 probe/traffic/recovery 三组断言、同容器 Ethernet socket 对端及 pc/OVMF 配置。离线 oracle 检查和真实 PCI 固件资源预检通过；尚未运行此次 Luna 翻译，不代表驱动验证通过。

已准备的 NE2000 实验可用 `./scripts/start-ne2000.sh` 启动或续跑；`--check` 只读检查准备状态，不调用模型。
