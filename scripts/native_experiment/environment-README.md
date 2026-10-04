# C2R Driver Experiment Environment

为 C2R-Driver 和 plain Codex baseline 提供相同的 Asterinas 内核、Docker 工具链、设备配置与原生测试。
只保存可复现定义；不保存 Docker 镜像副本、编译缓存、工作树或原始 Rust 驱动备份。

已验证的四项任务：**VirtIO RNG、VirtIO Block、VirtIO Network、NVMe PCI**。
原始基线的构建/启动/测试均通过；分别扣掉单个驱动后的内核仍能构建启动，对应设备测试失败。
VirtIO Vsock 因验证主机缺少 `/dev/vhost-vsock` 排除。
这些是环境控制结果，**尚未运行此套件上的模型翻译或独立私有评测**。

- [完整环境、隔离和使用协议](docs/experiments/NATIVE_DRIVER_SUITE.zh-CN.md)
- [固定版本与任务配置](configs/experiments/native-drivers.json)
- [真实验证结果摘要](validation/local-20261004.json)
- [工程检查与限制](docs/audits/NATIVE_ENVIRONMENT_20261004.md)
- [代码来源清单](PROVENANCE.json)

## 使用现有环境

要求 Linux/WSL2、Python 3.11.8+、Git、Docker、KVM。Network 还需要 `/dev/net/tun`。
直接复用本机已有源码仓库和镜像。Python 准备器仅用标准库，不需要安装 DPF；
DPF 的实际翻译控制器单独安装。

```sh
export SUITE="$PWD/../experiments/native-suite"
python3.11 scripts/native-experiment.py prepare --root "$SUITE" \
  --source /path/to/linux --target /path/to/original-asterinas --qemu /path/to/qemu
```

固定镜像是 `asterinas/dev:0.18.1-20260901`，准备器核对实际 image ID。
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

# Codex：同一输入 seed、镜像与原生测试；提示词保存在生成的 PROMPT.md 中。
python3.11 scripts/native-experiment.py trial --root "$SUITE" \
  --driver virtio-rng --method codex --qemu /path/to/qemu
"$SUITE/trials/codex/virtio-rng/01/launch.sh" --model MODEL
```

Codex 目录内 `build.sh` 构建，`check.sh` 对同一个 ISO 完整运行原生集合；每次检查保留独立收据。
DPF 的工作者使用相同公开用例，最终验收绑定控制器发布的同一最终产物。
正式比较前还需固定模型、推理强度、权限、预算和知识输入。此仓库不代替实验设计。

## 为什么没有多个 Asterinas 实验分支

某一任务的分支仍保留其他驱动的原实现；把这些分支放在同一个模型可读远端，会泄漏答案。
本仓库分发确定的移除脚本，在本地生成**每任务独立对象库、无原历史的根提交**。
只把当前任务的 seed 和 shared 资产提供给工作者；`operator/`、其他任务 seed 和原始仓库留在
操作者域。正式实验的宿主权限隔离需另外部署，不能只靠提示词。

## 协作和存储

- Git 只保存脚本、配置、文档和验证摘要。构建时按固定镜像复用工具链，不复制 Rustup。
- SDK一份，共享下载缓存；每个候选保留必要工作树和最终产物，串行构建后清理中间物。
- 本仓库的 `src/driver_port_factory/platform/` 只含共享容器/串口运行器，不是完整 DPF。
  DPF 集成时核对运行器身份，防止两个方法悄悄使用不同测试环境。
- 更新配置或运行器后，重新验证受影响控制并发布新版本。保留失败记录，不把环境故障当作负向通过。
- Python 标准库准备器可离线使用已备好的源码、镜像和下载缓存；首次依赖缺失时需要联网下载。
