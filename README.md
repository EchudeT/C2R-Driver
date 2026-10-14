# Driver Port Factory

将 Linux C 驱动迁移到 Asterinas（星绽）Rust 内核的研究框架。模型从完整 Linux 源码中查找驱动及内核上下文，完成分析、语义适配、功能实现和源码自检；控制器管理进度、知识检索、构建、公开验收与实验记录。

## 从哪里开始

| 需求 | 使用入口 |
| --- | --- |
| 跑 Factory/Codex 对照，或只跑其中一种方法 | [driver-experiment-env](https://github.com/C2R-Driver/driver-experiment-env) 的 `scripts/experiment.py` |
| 配置中转站、key、模型，安装或复用统一实验环境 | 同一实验入口；见[组员操作指南](https://github.com/C2R-Driver/driver-experiment-env/blob/main/docs/experiments/TEAM_RUNNER.zh-CN.md) |
| 调试 Factory，手动启动或继续已有迁移 | 本仓库的 `scripts/run-experiment.sh` / `resume-experiment.sh` |
| 查看架构、知识库或科研贡献 | [架构](docs/workflow/ARCHITECTURE.md)、[知识库](docs/knowledge/README.md)、[科研汇报](docs/research/FACTORY_RESEARCH_REPORT.zh-CN.md) |

本仓库提供翻译框架；环境仓库提供方法间共享的输入、Docker/设备配置、公开测试、隔离启动和统计。组员跑论文实验时统一使用环境仓库入口，无需另写启动脚本。

当前 main 已合入 `experiment/package-execution-focus-20261012`，调整当前工作包的开发检查范围、
连续诊断与必要交回，并索引同会话未变化的长执行说明；保留 B/C/R/P、源码引用和完整最终
验收。详见[分支调整说明](docs/workflow/ARCHITECTURE.md#当前实验分支的实现交付调整)。
本轮 Luna/Sol 六驱动第一遍已完成并记录，费用与成绩以环境仓库的冻结实验表为准；历史实验保留原协议。

当前版本提供可选的 native 网络抓包及 QEMU trace 工具，保持原设备、测例与最终验收。
Sol 冻结检查点支持旧任务的显式 netdev 绑定；Luna 保留原检查点，工具接入差异已记录。
见[网络诊断用法](docs/platform/NETWORK_DIAGNOSTICS.zh-CN.md)。

## 本轮冻结实验交接

组员运行 Codex＋goal 请使用环境仓库的[冻结操作指南](https://github.com/C2R-Driver/driver-experiment-env/blob/main/docs/experiments/FROZEN_CODEX_RUN.zh-CN.md)。Factory 两个实际检查点为 `fse-20261013-luna`（`4114e32`）与 `fse-20261013-sol`（`a9c8245`）。旧 main 保存在 `backup/main-before-package-execution-20261014`；当前 main 已采用实验分支实现，冻结复跑仍按上述标签取版本。环境仓库使用 `fse-20261013-runner` 标签；已校准的 native/public suite 继续复用，不能用后来统一目录的新环境替换。

## 快速开始：跑一组实验

需要 x86_64 Linux、Python 3.11.8+，以及可用的 Docker/KVM。下面按两个仓库放在同级目录示例；已有仓库直接复用。

```sh
git clone https://github.com/C2R-Driver/driver-port-factory.git
git clone https://github.com/C2R-Driver/driver-experiment-env.git
cd driver-experiment-env

# 创建本机配置，隐藏输入 key。
python3.11 scripts/experiment.py init --ask-key
# 编辑 .local/team.json：中转站、模型、Factory 路径、已有源码与 suite 路径。
python3.11 scripts/experiment.py install
python3.11 scripts/experiment.py doctor --host-only --driver virtio-rng
python3.11 scripts/experiment.py prepare-env --driver virtio-rng

# 只跑 Factory；默认不限制翻译费用或时长。
python3.11 scripts/experiment.py run \
  --driver virtio-rng --trial alice-rng-factory-01 --method factory
```

`install` 复用已有 Factory、虚拟环境、Codex CLI 和镜像。缺少系统依赖时，支持 Debian/Ubuntu 的 `install --system`；Python 3.11 应提前安装。首次 `prepare-env` 可能下载完整源码、准备公共资产并运行环境控制测试；已有发布环境直接复用。

默认配置为 `https://ai.wavepulse.live/v1`、`gpt-5.6-luna`、`medium`，可在 `.local/team.json` 修改。key 存在忽略的本地文件（权限 600），或通过 `C2R_API_KEY` 提供，不进入配置协议或 Git。中转站需支持 Codex 使用的 Responses API。实验固定 Codex CLI 0.160.1。

## 对照、监视与统计

以下命令仍在 `driver-experiment-env` 执行：

```sh
# 同一驱动的 Factory / 纯 Codex＋goal 对照，默认串行。
python3.11 scripts/experiment.py run --driver virtio-rng --trial alice-rng-pair-01
# 资源充足时，在上述命令后加 --parallel；仅 baseline 则加 --method codex。

python3.11 scripts/experiment.py status --driver virtio-rng --trial alice-rng-pair-01
python3.11 scripts/experiment.py export --driver virtio-rng --output results/team/native.csv
python3.11 scripts/experiment.py export --driver evbug --output results/team/public.csv
```

六个任务为 `evbug`、`virtio-rng`、`virtio-blk`、`virtio-net`、`nvme-pci`、`ne2k-pci`。两种方法共享该任务冻结的 Linux/目标 commit、官方镜像、设备、模型、推理等级、费率和公开断言；各自工作区与日志独立。baseline 只使用通用工具和上游命令，不获得 Factory 自写的迁移/检查工具。

`run` 才会调用付费模型。`prepare` 可提前生成输入和协议供审阅；`status`、`export` 读取已有记录。每次独立重复使用新 trial ID；已启动 trial 不覆盖、不自动续跑。串行或并行策略应在研究批次内保持一致。长任务可在 tmux 中运行。

输出位于 `<suite>/comparisons/<trial>/<driver>/`，包括共同 `protocol.json`、各方法的 `run.json`、`result.json` 和日志。Factory 工作区位于 `<suite>/trials/dpf/<driver>/<trial>/run/`。使用本仓库监视器可查看该路径：

```sh
# 在 driver-port-factory 中执行；替换为实际 Factory run 路径。
./scripts/watch.sh /path/to/suite/trials/dpf/virtio-rng/alice-rng-pair-01/run
```

左侧展示阶段，右侧展示模型活动。CSV 记录起止时间、总耗时、翻译与外部验收耗时、token、费用估计、编译/启动/初始化及公开测试成绩，失败样本也保留。导出按 suite 汇总；native/public 两套环境分别导出。私有评分另行执行，不反馈给模型修复，见[评分协议](https://github.com/C2R-Driver/driver-experiment-env/blob/main/docs/experiments/PARAMETER_VARIANT_SCORING.zh-CN.md)。

## Factory 直接开发用法

托管构建、测试和诊断工具采用[直接调用](docs/platform/DIRECT_MANAGED_TOOLS.zh-CN.md)，
避免外层 `exec` 提前返回导致模型反复等待；历史冻结实验仍使用各自检查点。

以下入口适合调试工作流和继续已有任务。论文对照使用上面的隔离启动器；直接启动不提供相同的宿主工作区隔离。

```sh
cd driver-port-factory
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
./scripts/run-experiment.sh --help
```

需要已配置模型服务和凭据的 Codex CLI、Git、Docker、`strace`。目标构建和 QEMU 在配置的 Docker 内运行，无需给宿主另装 Rust 工具链。仓库自带 `skill/`，通常不需要另指定 Skill。

使用已准备的 evbug suite、完整 Linux 仓库和空心目标，示例：

```sh
export DPF_NATIVE_SUITE=/path/to/public-suite
export DPF_REPOSITORY_PINS=/path/to/repository-pins.json
./scripts/run-experiment.sh \
  --workspace /path/to/development/evbug-01 \
  --source-platform linux --target-platform asterinas \
  --driver-name evbug \
  --catalog configs/drivers/linux-evbug.catalog.json \
  --model gpt-5.6-luna \
  --platform-image asterinas/dev:0.18.1-20260901 \
  --platform-accelerator kvm \
  --local-source-repository /path/to/public-suite/seeds/evbug/source \
  --local-target-repository /path/to/public-suite/seeds/evbug/target \
  --local-qemu-repository /path/to/pinned-qemu
```

路径、pins 和镜像必须匹配实际 suite。pins 指定 source、target、qemu 的 URL 与完整 commit，格式见[获取说明](docs/workflow/ACQUISITION.md)。不要使用浮动 HEAD；较新 Linux 版本可能已移除 evbug。`--model` 只选择模型，推理强度来自 Codex 配置或 `--codex-bin` 包装器；统一实验入口会为两组冻结它们。

```sh
./scripts/status.sh /path/to/development/evbug-01
./scripts/costs.py /path/to/development/evbug-01
./scripts/transcript.sh /path/to/development/evbug-01 driver_implementation
./scripts/resume-experiment.sh --workspace /path/to/development/evbug-01 --model gpt-5.6-luna
```

开发续跑保留已有实现；同一 workspace 只能有一个控制器。正式实验若人工续跑，必须单独记录干预、耗时与累计费用，不能当成新的独立样本。阶段按依赖推进，阶段编号不代表严格串行；`controller=STOPPED` 应结合结果判断是否完成。

## 工作流与知识库

联合分析形成来源行为、粗路径、目标适配与契约；控制器选取粗粒度工作包，工作者在连续会话内按需读源码、实现和检查。一个完整的小驱动可以只有一个工作包，注册/初始化/通知/清理不会自动拆成多个模型回合。账本中有阶段不等于必须进行一次模型调用。

模型负责语义自检，固定公开断言由我们预置，模型可适配被测接口。收尾针对同一个最终版本与 ISO 完整执行公开套件一次；已有完整最终批次直接读取，包括失败结果。临时路径或报告变化不触发复测。独立模型审阅默认关闭，可显式启用 `--analysis-review --final-evidence-review`。

知识库保存可检索的原始材料、分析资料及有来源的迁移经验，为当前任务和后续驱动提供按需检索，而非要求每轮读全库。开发中可配置共享经验库：

```sh
export DPF_SHARED_KB=/path/to/shared-driver-knowledge
# 设置后启动新的开发任务；查看任务学习记录：
.venv/bin/dpf knowledge learning /path/to/development/evbug-01
```

模型通过 `knowledge_learn(lesson, conditions, sources)` 提交经验，通过最终验收后发布。跨驱动积累与知识库消融应固定资料来源、快照和实验顺序。当前统一对照为 `cold-external-kb`：保留任务内分析知识，关闭外部共享 KB；不能认为给环境变量就能改变该冻结实验条件。详见[知识库文档](docs/knowledge/README.md)。

## 结果位置与当前能力

| Factory workspace 内路径 | 内容 |
| --- | --- |
| `.dpf/worktrees/`、`work/` | 获取的源码视图与可写目标；实际路径以 acquisition 记录为准 |
| `.dpf/project.json`、`.dpf/run.sqlite3` | 配置、阶段和事件 |
| `.dpf/codex/` | 提示词、模型输出、调用用量 |
| `.dpf/experiments/`、`.dpf/public-qemu/` | 检查收据与最终公开批次 |
| `.dpf/cas/` | 内容寻址产物 |

六项探索性 Factory/Codex 对照和参数变种私有评分已记录，见环境仓库的[科研数据表](https://github.com/C2R-Driver/driver-experiment-env/blob/main/results/paper-comparison-table.zh-CN.md)。四个 native 驱动已验证原始正向与空心负向控制；evbug/NE2000 使用原始目标中已缺失的驱动与固定公开套件。Vsock 因缺少所需宿主设备未纳入。

这些结果有明确版本和设备边界，公开 PASS 不证明完整语义等价；参数变种评分也不等于覆盖新功能的独立盲评。统一入口完成了离线与本机隔离检测，全新机器安装流程仍需部署验证。

## 开发与文档

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest
ruff check src tests scripts
ruff format --check src tests scripts
```

测试默认离线，不启动付费模型。仓库存在历史全量检查债务，局部通过不等于全量通过。

- [文档导航](docs/README.md)、[阶段指南](docs/workflow/STAGE_GUIDE.md)、[平台环境](docs/platform/README.md)
- [驱动 catalog](configs/README.md)、[实验文档](docs/experiments/README.md)
- [科研汇报](docs/research/FACTORY_RESEARCH_REPORT.zh-CN.md)、[知识库](docs/knowledge/README.md)
- `src/driver_port_factory/data/prompt-packs/default/`：模型提示词；`docs/history/`：历史设计
