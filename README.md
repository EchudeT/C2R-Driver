# Driver Port Factory

将 Linux C 驱动迁移到 Asterinas（星绽）Rust 内核的实验工作流。模型负责分析、实现和源码自检；控制器负责环境、进度、公开测试和运行记录。支持暂停续跑、逐工作包连续实现、按需知识检索及跨驱动经验积累。

## 安装

需要 Python 3.11+、Git、Codex CLI、Docker、`strace`；本项目已验证的 Asterinas 路线使用 x86_64、KVM 和容器内 OVMF。先配置 Codex 的模型服务和凭据，凭据不要提交到仓库。

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,codex]'
codex --version
docker info
test -e /dev/kvm
```

Skill 随仓库放在 `skill/`，通常不需要另传路径。离线仓库和 Docker 镜像由操作者预先提供；容器内构建和启动，不依赖宿主 Rust 工具链。

## 启动翻译

下面以已提供固定公开测试的 evbug 为例。将镜像和本地仓库路径替换为本机实际配置。

```sh
./scripts/run-experiment.sh \
  --workspace /path/to/experiments/evbug-01 \
  --source-platform linux --target-platform asterinas \
  --driver-name evbug \
  --catalog configs/drivers/linux-evbug.catalog.json \
  --model gpt-5.6-luna \
  --platform-image asterinas/dev:0.18.1-20260805-dpf-ovmf-bar-v1 \
  --platform-accelerator kvm \
  --local-source-repository /path/to/linux \
  --local-target-repository /path/to/asterinas \
  --local-qemu-repository /path/to/qemu \
  --max-model-cost-usd 10
```

**复现实验必须固定版本。** evbug 的较新 Linux 版本可能已移除此驱动，不能直接使用浮动 HEAD。使用原实验的 `repository-pins.json`，或按[仓库获取说明](docs/workflow/ACQUISITION.md)制作包含 source、target、qemu 三个角色、URL 和完整 commit 的文件，再设置：

```sh
export DPF_REPOSITORY_PINS=/path/to/repository-pins.json
```

模型推理强度在 Codex 配置或传入的 `--codex-bin` 包装器中设置；`--model` 只选择模型。费用上限在模型调用之间检查，单次长调用可能超额，金额是本地估算而非实扣账单。

分析审阅和最终模型审阅默认关闭。若实验协议明确需要，可加 `--analysis-review --final-evidence-review`。审阅设置随新 workspace 冻结。

一个工作包可以包含完整的小驱动；路由里的注册、初始化、通知、清理不会被自动拆成多个实现回合。实现阶段在同一会话内读源码、写代码和修复。最后由控制器独立运行预置公开测试。

## 观察、续跑、查看结果

```sh
./scripts/watch.sh /path/to/experiments/evbug-01
./scripts/status.sh /path/to/experiments/evbug-01
./scripts/costs.py /path/to/experiments/evbug-01
./scripts/transcript.sh /path/to/experiments/evbug-01 driver_implementation
./scripts/resume-experiment.sh --workspace /path/to/experiments/evbug-01 \
  --model gpt-5.6-luna
```

监视窗口左侧显示阶段，右侧显示模型活动。`run-experiment.sh` 在前台运行，可放进 tmux；不同 workspace 可并行，同一 workspace 只允许一个控制器。

阶段按依赖推进，编号不是严格串行顺序；例如预先准备环境后，08 可以先于07通过。`controller=STOPPED` 只表示当前没有控制器进程，需结合阶段状态和日志判断是已完成还是失败退出。

每次运行的重要文件：

| 位置 | 内容 |
| --- | --- |
| `work/target/` | 实现工作树 |
| `.dpf/project.json` | 冻结请求与配置 |
| `.dpf/run.sqlite3` | 阶段与事件记录 |
| `.dpf/codex/` | 模型调用、输出和用量 |
| `.dpf/experiments/` | 检查命令、输出和收据 |
| `.dpf/public-qemu/` | 最终公开验收批次 |
| `.dpf/cas/` | 内容寻址产物 |

不要手工改数据库或 CAS。默认运行有15个账本阶段，并不意味着15次模型调用。收尾测试阶段由程序执行，正常通过时不再调用模型确认。已有固定套件按同一最终候选完整执行一次；公开 PASS 仅表示该测试范围通过。

## 知识库

知识库按当前问题检索，不要求每轮读全库。可选共享经验库：

```sh
export DPF_SHARED_KB=/path/to/shared-driver-knowledge
# 之后按上面的命令启动新实验
.venv/bin/dpf knowledge learning /path/to/experiments/evbug-01
```

模型通过 `knowledge_learn(lesson, conditions, sources)` 提交有来源的经验，通过最终验收后发布。对照实验必须事先固定知识快照，避免后跑方法读到前一次候选或答案。详见[知识库文档](docs/knowledge/README.md)。

## 原实现移除实验与 Codex baseline

RNG、Block、Network、NVMe 的统一环境与原生测试入口见[原生驱动实验协议](docs/experiments/NATIVE_DRIVER_SUITE.zh-CN.md)。计划第五项 Vsock 因本机缺少 `/dev/vhost-vsock` 暂不启用。

该实验与上面的 evbug/NE2000 固定公开套件使用不同的、明确冻结的内核版本。不要混用旧镜像、旧工作树或曾翻译成功的候选。原始基线正向检查、可启动空心基线负向检查、DPF/Codex 输入副本和最终公共验收使用同一套方法无关工具。

## 当前验证范围

- 已有 `pvpanic-pci`、`evbug`、`ne2k-pci` 的预置设备测试；模型适配接口，不修改断言。
- 2026-10-03/04 的 luna-medium evbug 和 NE2000 运行均完成最终公开三项测试。NE2000 实现约2.5小时，仍有明显效率改进空间。
- 其他驱动的 catalog 只描述输入身份，不代表已完成翻译或验收。
- 原生移除实验使用未改写的星绽测例；准备状态、正负控制与覆盖限制见实验文档。
- 公开测试、自检和可选审阅不能替代独立私有评测，也不能证明所有硬件行为正确。

## 开发与文档

```sh
.venv/bin/pytest
ruff check src tests scripts
ruff format --check src tests scripts
```

测试默认离线，不启动付费模型。仓库存在历史全量检查债务；具体检查结果应如实记录，不能把局部通过写成全量通过。

- [文档导航](docs/README.md)
- [架构](docs/workflow/ARCHITECTURE.md)与[阶段指南](docs/workflow/STAGE_GUIDE.md)
- [平台环境](docs/platform/README.md)
- [驱动 catalog](configs/README.md)
- `src/driver_port_factory/data/prompt-packs/default/`：可审阅的模型提示词
- `docs/history/`：过去的设计与优化记录；`docs/audits/`：原始审计证据
