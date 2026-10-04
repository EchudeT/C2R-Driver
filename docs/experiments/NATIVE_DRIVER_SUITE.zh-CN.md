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

```sh
# 在环境分发仓库中执行；导出目录必须在该 checkout 之外。
python3.11 scripts/export-task.py virtio-rng /path/to/worker/rng-01
cd /path/to/worker/rng-01
./prepare.sh
# 或复用已有环境：./prepare.sh --reuse /path/to/verified-runtime
./build.sh
./check.sh
```

导出目录自带 `source/`、`target/`、公共运行器、`PROMPT.md` 和入口脚本，不保留分发库 Git 历史。
入口会在 `.runtime/` 中登记本机共享环境位置；这些运行数据不提交 Git。
初始 target 是空心基线，`check.sh` 因缺少驱动而失败是预期结果，不能误标为翻译通过。
在支持 goal 的 Codex 会话中以该目录为工作目录，按下面定义设置目标即可开始实现。
只有需要重新制作数据集时，才需要下一节的原始 Linux/Asterinas/QEMU 操作者仓库。

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

## 启动 DPF 或纯 Codex＋goal

创建启动目录不调用模型，也不预生成所有方法的工作树。DPF 在实际启动后由采集阶段创建工作树；
Codex 仅为所选任务创建 source/target。每个 trial 已存在时拒绝覆盖；重复实验用 `--trial 02` 等新编号，仍复用相同 suite 和 seed，
不重新准备环境。默认编号为 `01`。

```sh
# DPF：生成 launch.sh、固定 repository-pins.json，尚未翻译。
.venv/bin/python scripts/native-experiment.py trial --root "$SUITE" \
  --driver virtio-rng --method dpf --qemu /path/to/qemu \
  --factory /path/to/driver-port-factory
# 显式执行此命令才产生模型调用；模型和预算由操作者指定。
"$SUITE/trials/dpf/virtio-rng/01/launch.sh" --model MODEL --max-model-cost-usd BUDGET

# Codex＋goal baseline：准备同一 seed、PROMPT.md 和 build.sh/check.sh，尚未调用模型。
.venv/bin/python scripts/native-experiment.py trial --root "$SUITE" \
  --driver virtio-rng --method codex --qemu /path/to/qemu
```

baseline 为**纯 Codex＋goal**。准备 trial 后，在支持 goal 的 Codex 会话中将该 trial
设为工作目录，读取 `PROMPT.md` 并显式建立目标：

> 将 source/ 中指定的 Linux C 驱动迁移到 target/，完成必要的星绽集成；
> 使用 build.sh 和 check.sh 持续实现、检查和修复，直到同一最终版本的完整公开测试通过，
> 或达到预先规定的实验预算。不得修改断言或读取被隔离的原 Rust 实现；失败时保留真实结果。

由 Codex 自行决定分析和修复过程，不接入 DPF 阶段、工作包规划、专用迁移 Skill、知识库或 reviewer。
相同的公开源码输入、官方 Docker 镜像、设备配置和原生测试入口属于共同实验基础设施。
goal 的完成状态不替代最终完整测试收据。

目前 `launch.sh` 只执行普通 `codex exec`，不自动创建 goal；直接执行它不能记作 Codex＋goal 实验。
当前方式是在支持 goal 的会话中显式建立目标，自动 goal 启动接线尚未实现。
已经验证独立环境运行器，尚未执行 Codex＋goal 端到端翻译。

DPF launcher 设置 `DPF_NATIVE_SUITE` 并冻结镜像、加速器与源码 pin；08只验证平台构建启动，
不能要求空心驱动功能通过。环境通过后预装原生测试，13用相同工具实现与自检，15运行完整最终集合。
模型不负责设计断言。原生任务不能沿用 evbug/NE2000 的旧镜像及平台路线。

Codex 可以用 `build.sh` 仅构建、`check.sh` 构建并完整检查；`case --case <配置中的原路径>`
可在已有产物上单跑一个用例。每次 `check` 留存独立结果，不覆盖历史记录。
操作者最终也应从同一 runner 执行完整 `check --target <候选目录>`，不能拿模型陈述代替验收。

同模型、推理强度、权限、预算、输入范围和知识快照需在正式比较前另行固定。当前脚本未内置
plain Codex 的美元预算控制。生成的 Codex launcher 与 DPF 开发模式一样使用
`danger-full-access` 和 `approval_policy=never`；只应在已隔离公开材料的工作者环境执行。

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

独立 Git 对象库解决输入仓库的历史泄漏，不是宿主文件系统的安全隔离。正式研究应在独立工作者
账户或机器上仅部署上述公开材料，禁止访问操作者目录及原实现远端。当前开发模式拥有广泛宿主
工具权限，不能据此声称已实现对恶意读取的强隔离，也不能把此准备工作称作独立盲评。

## 存储和协作

GitHub 提交任务源码、配置、工具、文档、验证摘要；不上传 Docker tar、Rustup、Cargo缓存、ISO、磁盘镜像、
运行工作树、凭据或单独的 ground truth 备份。团队复用同一镜像版本，SDK一份、下载缓存两卷，按需生成工作区。
默认串行构建，`clean` 删除显式目标的编译缓存、OSDK包装中间物和已打包的临时 rootfs，
保留最终 ISO、源码与收据，不删除整个 Docker cache。

本机四任务准备数据约1GB（含四个 seed、原始及空心控制 ISO），不含已经存在的 Docker 镜像和
共享下载缓存；构建峰值另计。不得将此数误报为首次安装全部工具链的大小。
WSL 删除文件不保证 D盘上的 VHDX立即缩小；本机 D盘仍只剩约5.2GB。离线压缩需关闭发行版，
未在实验运行过程中执行。当前源码准备没有再复制数十GB环境。
