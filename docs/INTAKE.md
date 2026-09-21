# 迁移需求与驱动身份门

任何仓库 clone、发布镜像下载、知识库索引或工具链准备之前，DPF 必须先把用户需求解析成唯一的源驱动和设备/总线范围。

## 状态机

```text
UNRESOLVED -> ANALYZING
ANALYZING -> CONFIRMED -> FROZEN
ANALYZING -> NEEDS_USER_CONFIRMATION -> WAITING_FOR_USER
WAITING_FOR_USER + answer -> CONFIRMED -> FROZEN
```

`WAITING_FOR_USER` 不是失败或 `BLOCKED`。问题、候选列表和答案写入同一个运行账本，重复执行 `intake analyze` 不会产生第二个相同问题。

## 候选解析

解析顺序为：

1. 精确源码路径或规范模块名；
2. 已版本化的轻量平台目录；
3. 本地现有源码索引；
4. 轻量官方元数据或 Codex 分析任务。

不得在这一阶段 clone 完整内核、下载镜像、手册或工具链。`SourceDriverResolver` 从一个或多个版本化 `DriverMetadataProvider` 合并候选，每个 provider 都记录平台、来源、版本和 SHA256。生产包不内置具体驱动目录；NE2000 目录只存在于 `examples/fixtures`。固定源码取得后仍需验证源码入口与设备表，发现实质冲突时重新打开身份门。

## 冻结输出

`migration_envelope` 至少包含：源/目标平台、用户原始名称、规范源驱动名、源码入口、设备系列、总线、候选设备 ID、包含范围、排除变体、QEMU 模型、确认依据和被选 candidate ID。

只有 `migration_envelope_freeze=PASS` 后，`repository_acquisition` 才会变为 `READY`，因此后续 acquisition 无法绕过身份确认。

## CLI

```sh
dpf intake analyze ./run \
  --request "把 Linux 的 NE2000 驱动迁移到星绽OS" \
  --catalog examples/fixtures/linux-ne2000.catalog.json
dpf intake show ./run
dpf intake answer ./run --candidate-id linux-ne2k-pci \
  --answer "选择 PCI ne2k-pci，排除 ISA 和 PCMCIA"
```

若目录中没有该驱动，可在回答时显式提供规范身份：

```sh
dpf intake answer ./run \
  --candidate-id manual-uart \
  --canonical-name my-uart \
  --source-path drivers/tty/serial/my_uart.c \
  --device-family "My UART" \
  --bus MMIO
```
