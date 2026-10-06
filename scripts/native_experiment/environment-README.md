# C2R Driver Experiment Environment

为 C2R-Driver 和**纯 Codex＋goal baseline** 提供相同的 Asterinas 内核、Docker 工具链、设备配置与原生测试。
集中提供已验证的任务源码与环境定义，不上传 Docker 镜像副本、编译缓存或运行产物。

已验证的四项任务：**VirtIO RNG、VirtIO Block、VirtIO Network、NVMe PCI**。
原始基线的构建/启动/测试均通过；分别扣掉单个驱动后的内核仍能构建启动，对应设备测试失败。
VirtIO Vsock 因验证主机缺少 `/dev/vhost-vsock` 排除。
这些是环境控制结果，**尚未运行此套件上的模型翻译或独立私有评测**。

- [Factory 与纯 Codex＋goal：启动、隔离、统计](docs/experiments/FACTORY_CODEX_COMPARISON.zh-CN.md)
- [四项提示词预览](docs/experiments/baseline-prompts/README.md)
- [完整环境、隔离和使用协议](docs/experiments/NATIVE_DRIVER_SUITE.zh-CN.md)
- [固定版本与任务配置](configs/experiments/native-drivers.json)
- [真实验证结果摘要](validation/local-20261004.json)
- [工程检查与限制](docs/audits/NATIVE_ENVIRONMENT_20261004.md)
- [代码来源清单](PROVENANCE.json)

## 仓库中的实际任务文件

| 任务 | Linux 源码 | 空心星绽源码 | 运行入口 |
| --- | --- | --- | --- |
| RNG | [source](source/linux) | [target](tasks/virtio-rng/target) | [build.sh](tasks/virtio-rng/build.sh)、[check.sh](tasks/virtio-rng/check.sh) |
| Block | [source](source/linux) | [target](tasks/virtio-blk/target) | [build.sh](tasks/virtio-blk/build.sh)、[check.sh](tasks/virtio-blk/check.sh) |
| Network | [source](source/linux) | [target](tasks/virtio-net/target) | [build.sh](tasks/virtio-net/build.sh)、[check.sh](tasks/virtio-net/check.sh) |
| NVMe | [source](source/linux) | [target](tasks/nvme-pci/target) | [build.sh](tasks/nvme-pci/build.sh)、[check.sh](tasks/nvme-pci/check.sh) |

表中脚本是操作者的环境与评测入口，不提供给纯 baseline 模型。每项还有 `prepare.sh`、`PROMPT.md`、`task.json`。目标树一次只扣掉对应一个驱动；
被扣掉的驱动尚未实现，因此构建应成功，而其功能验收在翻译前应失败。

## 纯 Codex＋goal 与 Factory 的边界

Factory 可以使用自己的迁移、构建、检查与知识库工具；纯 baseline 只使用通用终端、Git、Docker
和内核上游 OSDK/QEMU。两组共享完整 Linux、空心星绽框架、固定官方镜像、设备、SDK/介质和
公开断言，最终评测与统计在模型之外进行。我们的 runner 不提供给 baseline 模型。

按需导出一个任务，避免复制全部 Linux 工作树：

```sh
python3.11 scripts/export-task.py virtio-rng /path/to/workers/rng-01 \
  --qemu /path/to/pinned-qemu
# 操作者在本仓库执行；已有本方案验证的环境就复用，不重新安装一套。
python3.11 scripts/task-runtime.py prepare --task /path/to/workers/rng-01 \
  --reuse /path/to/verified-native-suite-or-runtime
```

导出只包含 `source/`、`target/`、`PROMPT.md`、`task.json` 和干净 Git 根提交。
不导出 `.environment/`、build.sh/check.sh/prepare.sh 或 Factory 程序。
模型从完整 Linux 自主寻找驱动及内核上下文，目标树包含原生公开断言。
环境资产由操作者准备并只读提供；不得给模型全任务分发库或原 Rust 实现。

正式比较请使用下一节的配对启动器，支持原生 goal、统一预算、本地费用估算、token、
开始/结束/总时长与 CSV。普通 codex exec 不算 Codex＋goal。

## 官方 Docker 和操作者重新制作环境

要求 Linux/WSL2、Python 3.11.8+、Git、Docker、KVM。Network 还需要 `/dev/net/tun`。
直接复用本机已有源码仓库和镜像。Python 准备器仅用标准库，不需要安装 DPF；
DPF 的实际翻译控制器单独安装。

```sh
export SUITE="$PWD/../experiments/native-suite"
python3.11 scripts/native-experiment.py prepare --root "$SUITE" \
  --source /path/to/linux --target /path/to/original-asterinas --qemu /path/to/qemu
```

Docker 使用**星绽 OS 官方推荐的预构建开发镜像** `asterinas/dev:0.18.1-20260901`。
这一版本与固定 Asterinas commit 的
[官方 README](https://github.com/asterinas/asterinas/blob/d4b407ca87de203f79c27c9a83ada1d8223fe280/README.md#for-kernel-developers)
及 [DOCKER_IMAGE_VERSION](https://github.com/asterinas/asterinas/blob/d4b407ca87de203f79c27c9a83ada1d8223fe280/DOCKER_IMAGE_VERSION)
一致。机器尚未安装时先下载，已有时直接复用：

```sh
docker pull asterinas/dev:0.18.1-20260901
```

实验直接使用该官方镜像，准备器核对实际 image ID；不要求自行制作派生 Docker 镜像。
可用 `--sdk /path/to/cargo-osdk` 复用已验证的 SDK；没有符合版本的 SDK 才构建一次。
新主机仍需获取镜像和源码，仓库的小体积不代表首次安装成本为零。
不要将原始 Asterinas 直接提供给翻译模型。

```sh
# 先正向验证。复用同一个原始基线，不并行启动四次构建。
for driver in virtio-rng virtio-blk virtio-net nvme-pci; do
  python3.11 scripts/native-experiment.py positive --root "$SUITE" --driver "$driver" || break
done
python3.11 scripts/native-experiment.py clean --root "$SUITE" --target "$SUITE/operator/reference"

# 每个任务只移除一个实现，创建可启动空心树；负向验证通过后才发布独立 Git seed。
for driver in virtio-rng virtio-blk virtio-net nvme-pci; do
  python3.11 scripts/native-experiment.py hollow --root "$SUITE" --driver "$driver" || break
  python3.11 scripts/native-experiment.py negative --root "$SUITE" --driver "$driver" || break
  python3.11 scripts/native-experiment.py publish --root "$SUITE" --driver "$driver" \
    --source /path/to/linux || break
  python3.11 scripts/native-experiment.py clean --root "$SUITE" \
    --target "$SUITE/operator/hollow/$driver/target" || break
done
python3.11 scripts/native-experiment.py status --root "$SUITE"
```

## 启动正式配对实验

复用已验证 suite，先设置 `WAVEPULSE_API_KEY`，不要把密钥写入提示词或 Git。
Factory 需要单独安装；本仓库只含共享环境运行器。安装 bubblewrap，使用本机 Codex 0.160.1
兼容的原生 goal 接口。启动说明和日志口径见[比较协议](docs/experiments/FACTORY_CODEX_COMPARISON.zh-CN.md)。

```sh
python3.11 scripts/compare-experiments.py prepare --root "$SUITE" \
  --driver virtio-rng --trial paper-01 --qemu /path/to/pinned-qemu \
  --factory /path/to/driver-port-factory --max-cost-usd 10 --max-seconds 1800
export PROTOCOL="$SUITE/comparisons/paper-01/virtio-rng/protocol.json"
# 以下才调用付费模型。
python3.11 scripts/compare-experiments.py run "$PROTOCOL" --method codex
python3.11 scripts/compare-experiments.py run "$PROTOCOL" --method dpf
# 只读现有证据，不调用模型或重跑验收。
python3.11 scripts/compare-experiments.py export --root "$SUITE" --output comparison.csv
```

默认 WavePulse / gpt-5.6-luna / medium；两组相同费率和预算。baseline 建立原生持久 goal，
不加自写模型继续循环。Factory 默认关闭独立 reviewer，可使用自身工具及本次分析的任务知识，
外部共享知识库为空。已有完整最终测试批次直接读，没有才由外部共同 runner 运行一次。

旧 trial 的 build/check 包装器和普通 exec launch.sh 仅供开发兼容。纯 baseline 的隔离视图
隐藏这些入口、Factory、宿主 HOME、其他任务及 Docker socket；只开放本任务的 Docker 挂载。
网络仍用于服务与依赖下载，不能声称阻止恶意下载公开答案。严格盲评需要额外出口限制。

本次只完成离线协议与本地隔离检查，未调用付费模型、编译内核或跑驱动测试。
原生 goal 翻译的端到端表现仍需真实 trial 验证；goal complete 不等于测试 PASS。

## 分发仓库与模型工作区

所有任务源码集中放在本仓库，方便查看、版本管理和协作。某项 target 仍包含其他驱动的原实现，
因此模型不能直接在这个全任务分发 checkout 中实验。使用 `export-task.py` 只导出选定任务，
获得独立的新 Git 对象库。工作者部署还应禁止访问分发 checkout、其他任务、operator 目录和
原始 Rust 仓库；干净 Git 历史本身不构成宿主权限隔离。

## 协作和存储

- Git 保存任务源码、脚本、配置、文档和验证摘要。构建时按固定官方镜像复用工具链，不复制 Rustup。
- SDK一份，共享下载缓存；每个候选保留必要工作树和最终产物，串行构建后清理中间物。
- 本仓库的 `src/driver_port_factory/platform/` 只含共享容器/串口运行器，不是完整 DPF。
  DPF 集成时核对运行器身份，防止两个方法悄悄使用不同测试环境。
- 更新配置或运行器后，重新验证受影响控制并发布新版本。保留失败记录，不把环境故障当作负向通过。
- Python 标准库准备器可离线使用已备好的源码、镜像和下载缓存；首次依赖缺失时需要联网下载。

## 完整 Linux 源码输入

仓库的 [`source/linux/`](source/linux/) 保存固定 commit
`587858367581b9c55c3690f4e63382ad622719d4` 的**完整 Linux 源码树**，
包括所有驱动、`kernel/`、`mm/`、`fs/`、`net/`、`block/`、`arch/`、头文件、文档与构建文件。
这是真实源码快照，不是裁剪集合、下载占位符或需要额外初始化的 submodule。
来源 commit 和原始 Git tree ID 记录在 `source/PROVENANCE.json`。

四个 `tasks/<driver>/source` 链接到这一份完整源码；仓库不保存四份 Linux 工作树。
`export-task.py` 导出的工作区将完整源码实体化为自己的 `source/`，不链接回全任务分发库。
每次独立导出需要约 1.6 GB 源码内容及其 Git 对象，请按实际实验需要导出，不提前复制全部 trial。

模型自行搜索驱动及相关调用者、子系统、同步/内存管理等内核实现；`source_entries` 仅为定位提示，
不限制阅读范围。DPF 和纯 Codex＋goal 都使用这个完整版本；限制的是驱动修改范围，
不是源码调查范围。按需读取，无需把整个内核载入提示词。

操作者通过 `native-experiment.py publish` 制作输入时，source seed 复用传入的完整 Linux
Git 仓库，并固定 acquisition ref；Codex trial 导出该 ref 的完整源码，DPF 获取同一 ref。
旧版裁剪输入不得作为这版完整上下文实验的等价输入。已有构建和正负控制仍用于说明目标环境，
不构成模型在完整源码输入下完成翻译的结果。
