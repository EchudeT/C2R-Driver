# 统一原生驱动实验环境

## 当前状态（2026-10-04）

准备中，尚未达到可启动正式翻译实验的状态。RNG 原始基线已完成构建、启动和原生测例
正向验证；Block、Network、NVMe 正在串行检查。尚未发布通过负向检查的空心基线。
Vsock 因宿主设备缺失排除。没有启动付费模型。

工具链直接复用固定镜像中的二进制，不复制 Rustup；SDK 只保留一份。编译关闭增量缓存和
调试信息，这两项对两种方法一致。公开检查构建一次 ISO，再在该 ISO 上运行各个原生测例，
测试间不重新编译。现阶段运行记录属于准备过程，不能冒称自主翻译结果。

## 统一的是定义，不是备份

团队在 GitHub 共享版本清单、准备/检查脚本、catalog、提示和协议。每台机器复用本机同一个
固定 Docker 镜像、同一套 SDK 和下载缓存；源码工作区按需创建，禁止为各阶段备份整个环境。
构建中间产物、磁盘镜像、模型凭据和原始 ground truth 不提交 GitHub。

- 固定 Linux：`587858367581b9c55c3690f4e63382ad622719d4`。
- 固定 Asterinas：`d4b407ca87de203f79c27c9a83ada1d8223fe280`。
- QEMU 源码依据：`2d3df8abca265c9bcc9e438d691d561592060998`；实际容器二进制版本和哈希另记录。
- 镜像：`asterinas/dev:0.18.1-20260901`，运行时解析不可变 image ID。
- x86_64、KVM、q35、OVMF、1 vCPU、2GB guest RAM，DPF 和 Codex baseline 相同。
- 清单：[native-drivers.json](../../configs/experiments/native-drivers.json)。

## 任务与原有测例

| 任务 | 移除范围 | 公开 oracle |
| --- | --- | --- |
| VirtIO RNG | `virtio/src/device/entropy` | `regression/device/hwrng.c` |
| VirtIO Block | `virtio/src/device/block` | `io/file_io/block_device.c`、`fs/ext2/file_io.c`、`fs/ext2/short_rw.c` |
| VirtIO Network | `virtio/src/device/network` | 原 iperf3 TCP、UDP RX、UDP TX host/guest 脚本 |
| NVMe PCI | `nvme/src` | `regression/device/nvme.c` |
| VirtIO Vsock | 本轮不启用 | 本机缺少 `/dev/vhost-vsock`；不换用模拟传输 |

路径以 Asterinas 为基准；前三个移除目录位于 `kernel/core/comps/`。
NVMe 当前计划只包含用户态回归，原嵌入 ktest 尚未接入，不宣称覆盖它们。
网络 benchmark 只提供对应配置的通信/吞吐观察，不等于全面功能或性能达标。

## 基线制作

1. 原始 commit 与设备文件清单/哈希只由操作者持有，无需复制多份原实现。
2. 在统一环境运行未修改的原生测例，原始基线全部通过后才处理该驱动。
3. 每项只移除一个驱动和必要注册点，保留总线、队列、DMA、IRQ、上层框架及其他驱动。
4. 必要调用点采用明确无设备分支，使空心基线能够编译和启动，不留下设备算法 stub。
5. 原测试在空心基线上必须不能通过。缺设备后 exit 0 的 skip 不能算通过；测试缺失或
   环境错误也不能伪装成有效负向证据。负向失败原因需有设备缺失依据。
6. 只发布一份独立 Git 对象库的 sanitized seed。不能把原库、旧构建产物或旧对话放进模型输入。
7. 正式启动某个方法时才从 seed 建工作区，不预生成所有方法/重复实验的副本。

## 公共命令入口

```sh
.venv/bin/python scripts/native-experiment.py --help
.venv/bin/python scripts/native-experiment.py status --root /path/to/native-suite
```

脚本提供 `prepare`、`positive`、`hollow`、`negative`、`publish`、`trial`、`check`。
目前为开发入口，全部正负控制完成前不要启动正式翻译。
`trial --driver virtio-rng --method codex` 按需建立单个工作区；`dpf` 使用同一 seed。
`check --target PATH` 允许公共验收器指向控制器实际工作树，不能由模型改写测试断言。

当前 DPF 原生环境自动接入仍需完成，不应直接用旧 evbug 环境路线启动这些任务。
正式 baseline 还需固定相同模型、推理强度、任务范围、工具权限、预算与公开测试；知识库输入
作为实验变量明确记录，不能将其他试验候选、ground truth 或答案经验混入共享库。

## 结果与独立评价

保留候选、最终测试结果、失败原因和用量，清理可重建编译缓存。公开测例不改写；最终执行
完整集合，不能拼接不同候选的 PASS。独立私有评价另设协议，本轮没有实施盲测。

## 存储处理

同一镜像及内核版本的 DPF 构建共用下载/工具链缓存；候选编译产物仍在各自工作区，按需清理。
新准备工具不再同时创建 DPF 和 Codex 的所有工作树。SDK 优先复用已有文件，缺失时才构建一次。采用串行验证并清理编译中间物；
不要预创建所有方法和重复试验的环境。WSL 内删除文件不一定缩小 D 盘上的 VHDX；宿主压缩需要关闭该发行版，不能在
仍挂载的虚拟磁盘上直接执行压缩。
