# OVMF 路线与环境执行重构

本次保持 OVMF，不切换 SeaBIOS，不热更新或续跑实验 05。实验 05 已在
2026-10-03 14:03（北京时间）触及调用边界预算停止：总估算 $12.5377104，
实现调用 $5.6071312、1699.159 秒，只交回第一个行为完成。费用不是供应商账单。

## 根因：设备 BAR 属性与固件解释不一致

官方镜像 `asterinas/dev:0.18.1-20260805` 中 QEMU 为 10.2.1。
未修改 Asterinas 基线、同一 OVMF、Q35/KVM 下，在 `BdsDxe: starting Boot` 后和
内核 shell 启动后暂停 CPU，读取 PCI 配置空间，pvpanic BAR0 都是 0；其他设备已分配。
对 BAR0 保存原值、写全 1、读取、恢复原值，结果为：

- 原值 `0x00000000`：非预取 32 位内存 BAR。
- sizing 读回 `0xfffffffe`：低位不再保持原声明的 BAR 属性。
- 恢复后仍为 `0x00000000`。

QEMU 的 `hw/misc/pvpanic-pci.c` 调用 `pvpanic_setup_io(..., 2)`；`hw/pci/pci.c`
的 `pci_register_bar` 根据区域大小设置 `wmask = ~(size - 1)`。
EDK2 `edk2-stable202508` 的 `PciParseBar` 按探测值 `Value & 7` 分类；这里得到 6，
进入保留类型分支 `PciBarTypeUnknown`。这解释了 OVMF 为什么没有正常分配该 BAR。
这不是“UEFI 普遍不支持 pvpanic”，也不是模型应该无条件扩展内核 MMIO 分配器的依据。

## 修复边界

`resources/platform/ovmf/pvpanic-small-bar.patch` 是本地兼容补丁，不冒称上游修复。
只匹配 vendor/device `1b36:0011`、BAR0、原属性低四位为 0、探测值 `fffffffe`。
将探测属性恢复为原声明的 32 位内存类型，并使用可表示的 16 字节 BAR 粒度。
地址仍由 OVMF 原 PCI 资源分配器分配；不硬编码地址，不扩大内核特权，不修改 QEMU
事件模型、生产驱动、测例断言。无法匹配这一签名的设备不适用该补丁。

构建固定 EDK2 commit `d46aa46c8361194521391aa581593e556c707c6e` 和子模块。
使用同一工具链分别构建未打补丁和打补丁固件，避免把工具链/版本变化误归因于补丁。
运行镜像是明确命名的官方镜像本地派生物，只覆盖使用的 `OVMF.fd` 并加入 provenance；
不能将派生镜像称为未修改官方发行镜像。正式对照的全部方法使用同一冻结镜像。

## 环境执行责任

准备好的 smoke 配方在调试和控制器验收中都走 `environment.managed_smoke`：

1. 校验本地镜像身份，使用不可变 image ID 创建唯一容器；KVM 设备来自显式平台选择。
2. 校验容器 ID、实际 image ID 和 workspace 挂载。
3. 在容器内先校验 strace，再跟踪探针及后代的 execve；轨迹在容器层，不靠 docker top。
4. 容器退出后检查真实退出状态，复制轨迹并保存原始命令/输出，最后删除容器及匿名卷。
5. 退出码、超时、当前输入身份和设备断言仍必须满足要求。QEMU --version 不证明设备执行。

Docker/采集/身份/清理失败保存 INFRASTRUCTURE attempt，并抛出 ControllerError，直接
离开付费恢复循环。探针断言失败保存 PROBE attempt，才进入正常模型修复。
“没有采集到镜像”不再被描述成“已观察到错误镜像”。非托管历史采集器仍用于其他明确路线，
其 Docker events stderr 现保留；不能把新路径的稳定性推广到旧路径。
本执行证据针对合作式工作者，不声称能隔离有权攻击控制器的恶意模型。

## 与 v2 成本的关系

v2 历史 pvpanic 共 523 秒、3 次模型调用（Luna，费用未配置），但它已经提供 crate、
OSDK 配置、测试 API、ktest 和 QMP 脚本，采用 SeaBIOS，并排除完整 panic/kexec、
内核 shutdown hook 和 PCI 热插拔。v1 的生产组件与钩子目标更宽，模型为 Sol。
不能把二者原始总时间作为相同任务下的架构效果。

v1 05 实现仍有 84 条 shell 命令、首次编辑前 30 条、命令输出 298330 字符、
6 次 platform build、4 次 run-case、3 次自编 ktest 脚本检查。
相较 04 的 132 条命令、首次编辑前 60 条，重复探索有所下降，但并未达到成本目标。
具体可改进点：

- 此次把 BAR 兼容性解决放到显式、可复用的平台准备中；不再每驱动生成通用分配器。
- 环境采集故障退出模型返修；不让模型改 Docker wrapper 加延时。
- 仍待独立实现/验证：受管 ktest 接口，避免重新安装 OSDK 和生成 Docker 脚本；构建前
  有界更新 Cargo.lock 后再冻结输入，不能简单放宽构建中源码改变检查。
- 实现工作包的初始 prompt 仍约 44 KB，虽换了行为模板，公共工具/路由/报告指令仍重复。
  后续应移除重复分派要求，但不能删除冻结义务和一个行为一回合的边界。

不宣称此次固件与采集修复已证明端到端费用降至 $10 以下；需新的完整冻结实验验证。

## 本轮验证与下一轮输入

离线全量 pytest：384 passed（650.90 秒）；新增/小型修改 11 个文件 Ruff check 和
format --check 通过。全仓库仍有 581 个既有 lint 问题、139 个文件待格式化，未做无关改写。
真实执行器测试：20 次短命 QEMU 捕获均成功，断言失败、超时、仅版本查询和错误镜像
四类结果都按预期区分。初次实测发现缺少 KVM 挂载，已按显式平台选择修复并重跑；
初次失败记录也保留。

同版本原版固件复现未分配 BAR；修正版在未修改 Asterinas 内核中分配 BAR，
支持能力字节读取及 panic/crash-loaded/shutdown 的真实 QMP 事件；双设备地址不重叠，
无设备配置仍正常启动。原版和派生镜像中的 QEMU 可执行文件 SHA256 相同。

本地可用新镜像：`asterinas/dev:0.18.1-20260805-dpf-ovmf-bar-v1`。
它是本地派生物，非上游发行版；选择它必须在新实验前冻结。保留 OVMF、KVM、原 QEMU，
不移植旧实验的驱动或 MMIO 分配补丁。完整证据见
[audits/ovmf-execution-2026-10-03/summary.json](audits/ovmf-execution-2026-10-03/summary.json)。

2026-10-03 14:44:49（北京时间）已启动第 06 轮：
`../experiments/pvpanic-behavior-sol-medium-20261003-06`，PID 1473497。
独立冻结控制器 347 个文件，gpt-5.6-sol/medium，原知识库快照，$10 调用边界预算，
禁用独立模型审查，保留实际最终验收。只读观察，不在运行中改动。
启动前清理 67 个旧实验 Rust 构建目录，实际释放约 52.2 GB；删除清单和边界保留在
同目录 audit 的 cleanup.json。源码、CAS、日志保留；已删除的构建路径不再可直接重放。
