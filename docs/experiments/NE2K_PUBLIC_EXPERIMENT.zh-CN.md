# NE2000 PCI / Luna medium 实验准备

状态：固定仓库、公开测试与执行适配已准备；完整目标内核构建及 Docker/KVM/OVMF 交互启动已通过并登记为 08 PASS。尚未启动 NE2000 付费翻译，尚无真实驱动 PASS。

身份沿用用户此前确认的 PCI NE2000（QEMU RTL8029 10ec:8029），不是 ISA/PCMCIA。
模型使用 gpt-5.6-luna / medium；源、目标与 QEMU 用显式 operator pins 固定：

- Linux：6d4a0f4ea72319c9a37c1a7191695467006dd272。
- Asterinas：d924a9635a66c7c3bb43e563eaafa2c61d6ee9d5。
- QEMU 源证据：2d3df8abca265c9bcc9e438d691d561592060998；实际执行器为固定镜像内 QEMU，另行记录版本。

沿用 asterinas/dev:0.18.1-20260805-dpf-ovmf-bar-v1、KVM 与固定 OVMF。
NE2000 场景使用 pc/i440fx，RTL8029 在 slot 5；其他驱动继续使用已有机器配置。
运行时 socket peer 与 QEMU 同容器，loopback 随机端口，Docker network=none。
不需要宿主 TAP、固定外部端口或外网，失败不切换环境路线。

预置公开测试见 `src/driver_port_factory/data/public-tests/ne2k-pci/`：
probe、traffic（96 帧多长度 + 16 帧 burst）、recovery（三次 stop/start 后往返）。
真正网络对端核对发出的帧并变换响应，内核测试经真实 AnyNetworkDevice 接口验证接收。
公开断言、刺激与 peer 归控制器；模型实现驱动和 /proc 调用接口。
完整源码义务包含 ne2k-pci 与共享 8390 实现，不能把公共流量子集冒充全部源语义。

借鉴 v2 的公共 wire 场景，但没有复制其翻译驱动或组件 ktest 流程。
当前 v1 目标版本的 send(&[u8]) / RxBuffer 与 v2 的 FreshTxPacket 不同，fixture 已针对
固定版本适配。PIO 接收的安全缓冲区适配留给实际实现，允许最小修改，不能伪造收包。
正常路径可以一个完整工作包完成；路线步骤、三个测例不产生三个强制实现回合。
实现后仍由最终验收对同一源码和 ISO 完整运行三个 case。

准备验证：离线 socket 分片/精确序列/独立响应/burst 检查及 case 安装检查通过。
真实 Docker/KVM/OVMF 短预检发现 RTL8029 于 bus0 slot5，I/O BAR=0xc000、size=256、IRQ=10。
该预检只证明固件资源分配，不证明目标内核能成功驱动网卡。
预检证据：`../experiments/ne2k-pci-luna-medium-20261003-01/preflight/`。

未声明已覆盖：物理克隆差异、ISA/PCMCIA、完整 TCP/IP、动态卸载/热插拔、所有硬件错误恢复、
全部并发交错。正式结果必须区分公开测试、独立评估与真实硬件。
启动清单准备在独立实验目录，controller/共享知识读取快照与干净模型 home 均独立；
读取库可复用已公开验证的跨驱动经验，成功后按现有策略写回公共库。

## 一条命令启动

在 `driver-port-factory` 根目录执行：

```sh
./scripts/start-ne2000.sh
```

该入口使用准备好的独立 controller、Python runtime、模型配置、固定仓库和公共测试，
前台启动；再次执行同一命令续跑原 workspace。执行日志追加到实验目录 `controller.log`，
模型输出和检查收据保留在 `run/.dpf/` 与目标工作区。
仅检查准备状态、不调用模型：`./scripts/start-ne2000.sh --check`。
运行仍使用现有 provider 凭据，网络/账户可用性以启动时实际响应为准。

操作者提前通过真实服务完成仓库采集与完整基线构建/启动验证；准备过程不调用模型，
保留 `prepare-environment.py`、`preparation.log` 和控制器账本。环境成功后以真实收据
完成 08，翻译从尚未完成的证据分析开始，不重复执行已完成的环境准备。
这些时间属于环境准备，不可在论文中隐去或冒充模型自主工作。

本次基线实测：构建 202.914 秒，启动检查 35.165 秒；环境准备模型调用 0 次。
