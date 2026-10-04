# C2R Driver Experiment Environment

为 C2R-Driver 和**纯 Codex＋goal baseline** 提供相同的 Asterinas 内核、Docker 工具链、设备配置与原生测试。
集中提供已验证的任务源码与环境定义，不上传 Docker 镜像副本、编译缓存或运行产物。

已验证的四项任务：**VirtIO RNG、VirtIO Block、VirtIO Network、NVMe PCI**。
原始基线的构建/启动/测试均通过；分别扣掉单个驱动后的内核仍能构建启动，对应设备测试失败。
VirtIO Vsock 因验证主机缺少 `/dev/vhost-vsock` 排除。
这些是环境控制结果，**尚未运行此套件上的模型翻译或独立私有评测**。

- [完整环境、隔离和使用协议](docs/experiments/NATIVE_DRIVER_SUITE.zh-CN.md)
- [固定版本与任务配置](configs/experiments/native-drivers.json)
- [真实验证结果摘要](validation/local-20261004.json)
- [工程检查与限制](docs/audits/NATIVE_ENVIRONMENT_20261004.md)
- [代码来源清单](PROVENANCE.json)

## 仓库中的实际任务文件

| 任务 | Linux 源码 | 空心星绽源码 | 运行入口 |
| --- | --- | --- | --- |
| RNG | [source](tasks/virtio-rng/source) | [target](tasks/virtio-rng/target) | [build.sh](tasks/virtio-rng/build.sh)、[check.sh](tasks/virtio-rng/check.sh) |
| Block | [source](tasks/virtio-blk/source) | [target](tasks/virtio-blk/target) | [build.sh](tasks/virtio-blk/build.sh)、[check.sh](tasks/virtio-blk/check.sh) |
| Network | [source](tasks/virtio-net/source) | [target](tasks/virtio-net/target) | [build.sh](tasks/virtio-net/build.sh)、[check.sh](tasks/virtio-net/check.sh) |
| NVMe | [source](tasks/nvme-pci/source) | [target](tasks/nvme-pci/target) | [build.sh](tasks/nvme-pci/build.sh)、[check.sh](tasks/nvme-pci/check.sh) |

每项还有 `prepare.sh`、`PROMPT.md`、`task.json`。目标树一次只扣掉对应一个驱动；
被扣掉的驱动尚未实现，因此构建应成功，而其功能验收在翻译前应失败。

## 直接运行纯 Codex＋goal baseline

操作者在分发仓库中导出所选任务，模型在导出目录中工作：

```sh
python3.11 scripts/export-task.py virtio-rng /path/to/workers/rng-01
cd /path/to/workers/rng-01
# 未安装时下载星绽官方开发镜像；已有时复用。
docker pull asterinas/dev:0.18.1-20260901
./prepare.sh
# 已有本方案验证的环境时改用下行，复用 SDK 和测试介质，不重建一套：
# ./prepare.sh --reuse /path/to/verified-native-suite-or-runtime
```

`source/`、`target/`、`build.sh`、`check.sh` 都会出现在这个工作区中。
导出工具只读取已提交的所选任务和公共运行器，建立无 remote 的新 Git 根提交；
不带分发仓库历史或其他任务。`prepare.sh` 可以直接从空心 target 准备 SDK 和测试介质，
不需要把原始完整星绽仓库提供给工作者。后续重复实验继续复用共享工具和下载缓存。

在支持 goal 的 Codex 会话中选择该工作目录，读取 `PROMPT.md` 并显式建立目标：

> 将 source/ 中指定的 Linux C 驱动迁移到 target/ 的星绽空心基线，完成必要集成；
> 使用 build.sh 构建、check.sh 完整验收，持续修复直到同一最终版本的全部公开测试通过，
> 或达到预先规定的实验预算。不得修改断言或读取被隔离的原 Rust 实现；如实报告剩余问题。

baseline 不接入 DPF 阶段调度、工作包、专用迁移 Skill、知识库或 reviewer。
goal 完成声明不替代测试收据；目前没有自动创建 goal 的启动器，旧 `launch.sh` 只调用普通
`codex exec`。环境已验证可独立运行，Codex＋goal 端到端翻译尚未执行。

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

## 启动翻译

准备 trial 本身不调用模型；只在显式执行生成的 `launch.sh` 时调用。
重复实验加 `--trial 02`，复用 suite 和 seed，不重新安装环境。

```sh
# DPF：固定版本、平台及公开断言由控制器接入。
python3.11 scripts/native-experiment.py trial --root "$SUITE" \
  --driver virtio-rng --method dpf --qemu /path/to/qemu --factory /path/to/driver-port-factory
"$SUITE/trials/dpf/virtio-rng/01/launch.sh" --model MODEL --max-model-cost-usd BUDGET

# Codex＋goal：先生成同一输入 seed 和工具入口，不在这里启动普通 exec。
python3.11 scripts/native-experiment.py trial --root "$SUITE" \
  --driver virtio-rng --method codex --qemu /path/to/qemu
```

实验 baseline 定义为**纯 Codex＋goal**：在支持 goal 的 Codex 会话中，以生成的
`$SUITE/trials/codex/virtio-rng/01/` 为工作目录，读取 `PROMPT.md`，明确建立并持续执行目标：

> 将 source/ 中指定的 Linux C 驱动迁移到 target/ 的星绽空心基线，完成必要集成；
> 使用 build.sh 构建、check.sh 完整验收，持续修复直到同一最终版本的全部公开测试通过，
> 或达到预先规定的实验预算。不得修改测试断言或读取被隔离的原 Rust 实现；
> 停止时如实报告剩余问题，不能把 goal 完成声明当作测试通过。

只提供任务、C 源码、空心目标与公共工具。Codex 自行分析、实现和修复；
baseline 不接入 DPF 阶段调度、工作包规划、专用迁移 Skill、知识库或 reviewer。
两种方法共用环境与原生断言，操作者最终核对实际测试收据。

**当前生成的 `launch.sh` 仅调用普通 `codex exec`，不会自动创建 goal。**
执行它不能记为已经运行 Codex＋goal baseline；目前需要在支持 goal 的会话中显式设置目标。
环境已验证可独立运行，Codex＋goal 的端到端翻译实验尚未执行。

Codex 目录内 `build.sh` 构建，`check.sh` 对同一个 ISO 完整运行原生集合；每次检查保留独立收据。
DPF 的工作者使用相同公开用例，最终验收绑定控制器发布的同一最终产物。
正式比较前还需固定模型、推理强度、权限、预算和知识输入。此仓库不代替实验设计。

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
