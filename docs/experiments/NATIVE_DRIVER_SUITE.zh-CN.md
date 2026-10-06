# 统一原生驱动实验环境

## 当前状态（2026-10-04）

四项实验的原始基线、可启动空心基线及 DPF 运行适配器均已验证；四个独立 Git seed 已生成。
没有启动付费翻译，本记录是环境准备结果，不是模型翻译成功率。

| 任务 | 原始基线：构建、启动、原生用例 | 空心基线：构建、启动，设备用例失败 | DPF 实际适配器复现正负控制 |
| --- | --- | --- | --- |
| VirtIO RNG | PASS | PASS | PASS |
| VirtIO Block | PASS | PASS | PASS |
| VirtIO Network | PASS | PASS | PASS |
| NVMe PCI | PASS | PASS | PASS |
| VirtIO Vsock | 排除：宿主缺少 `/dev/vhost-vsock` | 未运行 | 未运行 |

本机准备目录为项目同级的 `experiments/native-suite-20261004/`。
证据保存在 `operator/<driver>/{positive,negative,adapter-validation,seed-audit}.json`。
`seeds/<driver>/{source,target}` 是可交给对应实验的输入；`operator/` 仅供操作者使用。
DPF 对固定测试的登记和最终断言保护已接入；全流程模型翻译尚未运行。

共享环境仓库：<https://github.com/C2R-Driver/driver-experiment-env>。
仓库集中分发 `tasks/<driver>/source`、`target`、可移植入口，以及配置、文档和验证摘要。
使用 `scripts/export-task.py` 导出单项工作区后再交给模型，不直接在全任务分发仓库中运行模型。

## 冻结环境

- Linux：`587858367581b9c55c3690f4e63382ad622719d4`。
- Asterinas：`d4b407ca87de203f79c27c9a83ada1d8223fe280`。
- QEMU 源码依据：`2d3df8abca265c9bcc9e438d691d561592060998`。
- Docker：星绽 OS 官方推荐的预构建开发镜像 `asterinas/dev:0.18.1-20260901`；校验 image ID
  `sha256:a2c8b011ebe635fc7257fe9edaf4135b76c278600374536539edd41125fb4dca`。
- 实际容器 QEMU：10.2.1；二进制 SHA256
  `924966a935f0429f99e093f4abaca04edfbdf8c6fa7b8b206145e1108b51c5b0`。
  源码用于分析依据，不宣称该二进制由上述源码 commit 构建。
- OVMF：`/root/ovmf/release/OVMF.fd`；SHA256
  `7d28bbc1bb6a8da1aeea3d04240b2b6da728a6445c3843b98df6c4d7e6503ccb`。
- x86_64、KVM、q35、1 vCPU、2GB guest RAM、Rust nightly-2026-07-21。
- 容器工具链直接复用；SDK 为 `OSDK_LOCAL_DEV=1` 构建的 cargo-osdk，按哈希复用。
- `CARGO_INCREMENTAL=0`、`CARGO_PROFILE_DEV_DEBUG=0`、构建并行度4，对两种方法一致。
- 配置源：[native-drivers.json](../../configs/experiments/native-drivers.json)。

镜像版本依据固定 Asterinas commit 的
[官方 README](https://github.com/asterinas/asterinas/blob/d4b407ca87de203f79c27c9a83ada1d8223fe280/README.md#for-kernel-developers)
和 [DOCKER_IMAGE_VERSION](https://github.com/asterinas/asterinas/blob/d4b407ca87de203f79c27c9a83ada1d8223fe280/DOCKER_IMAGE_VERSION)。
直接下载并复用该官方镜像，本实验不要求自行构建派生 Docker 镜像；DPF 与 Codex＋goal 使用同一 image ID。

使用原始 `tools/qemu_args.sh normal` 的设备配置；统一为无图形串口交互和 OVMF/KVM。
原始与空心基线均只统一调整 `OSDK.toml` 的 init 参数以进入交互 shell，不更改测试断言。
网络使用隔离容器内的 TAP，其他设备用例使用原配置的 user 网络。两种方法采用相同规则。

## 用例与覆盖

| 任务 | 移除范围（相对 `kernel/core/comps/`） | 保留的星绽原生 oracle |
| --- | --- | --- |
| RNG | `virtio/src/device/entropy` | `regression/device/hwrng.c`，15个 FN_TEST |
| Block | `virtio/src/device/block` | `io/file_io/block_device.c`、`fs/ext2/file_io.c`、`fs/ext2/short_rw.c`，共8个 FN_TEST |
| Network | `virtio/src/device/network` | iperf3 原 TCP、UDP RX、UDP TX 的 host/guest 脚本 |
| NVMe | `nvme/src` | `regression/device/nvme.c`，1个 FN_TEST |

回归路径以 `test/initramfs/src/` 为前缀；network 位于其 `benchmark/iperf3/` 下。
原 C 测试与 host/guest 脚本不改写，只打包进共同 initramfs。
FN_TEST 必须逐函数有实际成功计数且无失败；exit 0 的 skip 不能算通过。
网络必须有真实、非零的 sender/receiver 传输结果，不能以 loopback 或仅启动成功替代。
这不是性能阈值评价；NVMe 内嵌 ktest、故障注入和独立私有测试尚未接入。

每次完整验收只构建一次 ISO，所有用例运行这个相同产物，测试之间不编译。
每例从同一套稀疏磁盘种子创建可写测试介质，防止上例磁盘状态影响下一例。
DPF 最终测试使用控制器发布的最终产物路径；Codex 使用同一 runner、构建参数与完整测试集合。

## 从已发布源码直接开始

操作者在环境仓库导出单任务输入，不给纯 baseline 导出自写工具：

```sh
python3.11 scripts/export-task.py virtio-rng /path/to/worker/rng-01 \
  --qemu /path/to/pinned-qemu
# 操作者在分发仓库执行，共享已有 SDK 和介质；不要交给模型执行。
python3.11 scripts/task-runtime.py prepare --task /path/to/worker/rng-01 \
  --reuse /path/to/verified-runtime
```

导出目录包含完整 `source/`、空心 `target/`、`PROMPT.md` 和任务元数据，创建新 Git 根提交。
没有 `.environment/` 或我们的 build/check/prepare 脚本。原生公开断言位于 target 中。
操作者提供固定 SDK/initramfs/磁盘介质，模型自行使用通用终端、Docker 与上游 OSDK/QEMU。
正式配对实验使用[比较入口](FACTORY_CODEX_COMPARISON.zh-CN.md)，它接入原生 goal、实际工作区隔离和统计。
初始空心 target 编译成功而设备功能失败，是环境负向控制，不是翻译成功。

## 新机器准备

需要 Python 3.11.8+、Git、Docker、KVM；网络任务还需要 `/dev/net/tun` 和容器 NET_ADMIN。
复用已有三个源码库及本地 Docker 镜像。缺失时只获取一次固定 revision；不为各方法复制工具链。
镜像缺失时可先执行 `docker pull asterinas/dev:0.18.1-20260901`，随后准备器会核对 image ID。
不要将操作者持有的原始 Asterinas 仓库作为翻译任务的 target 参数。

在 DPF 仓库中：

```sh
export SUITE="$PWD/../experiments/native-suite-20261004"
.venv/bin/python scripts/native-experiment.py prepare --root "$SUITE" \
  --source /path/to/linux --target /path/to/original-asterinas --qemu /path/to/qemu
```

独立环境仓库可直接用 `python3.11 scripts/native-experiment.py`，准备和原生检查不依赖 DPF。
若已有本方案验证的 SDK，可加 `--sdk /path/to/cargo-osdk`，按精确哈希核对；否则只构建一次 SDK。
`prepare` 拒绝覆盖已有 suite，不用它续跑已准备环境。

```sh
# 正向控制共享唯一原始基线，串行运行。
for driver in virtio-rng virtio-blk virtio-net nvme-pci; do
  .venv/bin/python scripts/native-experiment.py positive --root "$SUITE" --driver "$driver" || break
done
.venv/bin/python scripts/native-experiment.py clean --root "$SUITE" \
  --target "$SUITE/operator/reference"

# 每个目标只移除一个驱动；验证后发布，再清理编译中间物。
for driver in virtio-rng virtio-blk virtio-net nvme-pci; do
  .venv/bin/python scripts/native-experiment.py hollow --root "$SUITE" --driver "$driver" || break
  .venv/bin/python scripts/native-experiment.py negative --root "$SUITE" --driver "$driver" || break
  .venv/bin/python scripts/native-experiment.py publish --root "$SUITE" --driver "$driver" \
    --source /path/to/linux || break
  .venv/bin/python scripts/native-experiment.py clean --root "$SUITE" \
    --target "$SUITE/operator/hollow/$driver/target" || break
done
.venv/bin/python scripts/native-experiment.py status --root "$SUITE"
```

失败留存日志，不自动切换环境或伪装成负向通过。修正准备工具后，保留失败收据再显式重试；
不要把整个环境复制成“备份”。`validate-adapter` 可在 DPF 安装后复现生产适配器控制，复用已有
ISO；独立环境仓库加 `--factory /path/to/driver-port-factory`。

## 启动 Factory 或纯 Codex＋goal

正式实验使用[统一比较入口](FACTORY_CODEX_COMPARISON.zh-CN.md)，不要直接执行旧 Codex launch.sh。
prepare 按需生成一对独立 trial，不调用模型；run 才开始付费翻译；collect/export 只读证据。

```sh
python3.11 scripts/compare-experiments.py prepare --root "$SUITE" \
  --driver virtio-rng --trial paper-01 --qemu /path/to/pinned-qemu \
  --factory /path/to/driver-port-factory --max-cost-usd 10 --max-seconds 1800
export PROTOCOL="$SUITE/comparisons/paper-01/virtio-rng/protocol.json"
python3.11 scripts/compare-experiments.py run "$PROTOCOL" --method codex
python3.11 scripts/compare-experiments.py run "$PROTOCOL" --method dpf
```

默认两组使用 WavePulse 的 gpt-5.6-luna、medium，费率和预算冻结进协议。
baseline 通过原生 thread/goal/set 建立持久 goal，不接入 Factory 工具、阶段调度、Skill、知识库或 reviewer。
Factory 可以使用自己的工具；本协议默认关闭独立模型 reviewer，外部共享知识库为空，保留任务内知识。
完整 Linux、空心目标、Docker 镜像、架构/设备/SDK/介质与最终测试断言保持一致。

旧 native-experiment.py trial 仍生成操作者 build/check 包装器和普通 exec 入口，保留用于开发兼容；
它们不是纯 Codex＋goal 实验入口。正式 baseline 隔离视图不会暴露这些脚本。
Factory 已有完整最终测试批次时直接读取，无论 PASS/FAIL；否则由模型之外的共同运行器收尾。
费用包含所有模型阶段与返修，不把部分实现成本当总成本；详见比较说明。

两组真实模型翻译尚未执行，已有正负控制证明环境适用，不证明方法效果。

## 干净基线与隔离

1. ground truth 只保存在操作者原始仓库和文件哈希清单，不给模型。
2. 每项从同一原始 commit 导出，只删除一项实现及直接注册，保留传输、virtqueue、DMA、IRQ等框架。
3. 上层具体类型引用采用无设备分支；NVMe 只保留空 crate 外壳，不能留下原算法 stub。
4. 空心树必须构建和启动；测试因缺少设备行为失败才构成有效负向控制，环境失败不算。
5. 每项导出到新的 Git 对象库，创建可复现的根提交；审核没有父提交、remote、alternates、
   不可达对象或被扣掉驱动原文件。Linux 来源亦从固定 commit 导出并记录上游 provenance。
6. 全部任务可以放在同一个操作者分发仓库；交给模型前必须只导出选定任务，创建新 Git 根提交，
   不保留分发仓库 remote 或其他任务历史。模型不能直接在全任务 checkout 中实验。
7. 只交付本任务的 seed、共同 SDK/initramfs 和公开用例。原始树、其他任务 seed、operator 日志、
   旧候选和答案知识不可进入工作者工作区。容器运行器只挂载当前 target 和 shared 资产。

独立 Git 对象库解决输入历史泄漏。正式比较入口进一步使用 mount/PID namespace 和任务私有
Docker 代理，隐藏宿主 HOME、原实现、其他任务、历史候选及 Docker socket；没有隔离能力就停止。
网络仍可用于模型服务和依赖下载，因此不声称阻止恶意下载公开原 Rust；严格盲评需另设出口限制。
这套公开环境与隔离机制不等同于独立私有盲评。

## 存储和协作

GitHub 提交任务源码、配置、工具、文档、验证摘要；不上传 Docker tar、Rustup、Cargo缓存、ISO、磁盘镜像、
运行工作树、凭据或单独的 ground truth 备份。团队复用同一镜像版本，SDK一份、下载缓存两卷，按需生成工作区。
默认串行构建，`clean` 删除显式目标的编译缓存、OSDK包装中间物和已打包的临时 rootfs，
保留最终 ISO、源码与收据，不删除整个 Docker cache。

本机四任务准备数据约1GB（含四个 seed、原始及空心控制 ISO），不含已经存在的 Docker 镜像和
共享下载缓存；构建峰值另计。不得将此数误报为首次安装全部工具链的大小。
WSL 删除文件不保证 D盘上的 VHDX立即缩小；本机 D盘仍只剩约5.2GB。离线压缩需关闭发行版，
未在实验运行过程中执行。当前源码准备没有再复制数十GB环境。

## 完整 Linux 源码输入

仓库的 `source/linux/` 保存固定 commit
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
