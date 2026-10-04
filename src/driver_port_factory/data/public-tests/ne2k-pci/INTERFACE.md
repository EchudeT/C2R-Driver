# NE2000 PCI 固定公开测试接口

Linux `ne2k-pci.c` + 共享 `8390.c/lib8390.c/8390.h` → Asterinas 真正 PCI/network 驱动。
固定目标 d924a9635a66c7c3bb43e563eaafa2c61d6ee9d5；此版本 AnyNetworkDevice.send
接收 `&[u8]`，receive 返回 aster_network::RxBuffer。不要照搬较新版本的 FreshTxPacket API。

## 模型负责

- 在 `kernel/core/comps/network/src/ne2k.rs` 实现驱动，必要时增加子模块及真实平台依赖。
  `pub fn register() -> Result<(), ostd::Error>` 通过真实 PCI registry 绑定 RTL8029，
  读取固件分配的 I/O BAR/legacy IRQ、从 PROM 读取 MAC，注册真实 `AnyNetworkDevice` 为 `ne2k0`。
- `pub fn stop()/start() -> Result<(), ostd::Error>` 可重复调用；保留 PCI 绑定，stop 停止收发并清理待处理状态，
  start 恢复运行。不要写死 BAR、IRQ 或 PROM MAC；MAC 的测试常量是设备配置的预期值。
- 网络 crate 的 lib.rs 导出 `pub mod ne2k; pub mod dpf_ne2k_public;`。驱动按测试调用注册，
  不在内核启动时提前注册第二份设备；现有 PCI/网络组件正常初始化。
- 提供 root 可写 `/proc/ne2k_test`；写入 `probe`、`traffic`、`recovery` 时只转调
  `aster_network::dpf_ne2k_public::run(command.trim())`，成功返回写入长度。
  错误命令返回错误。接口不能生成测试成功标记、断言或接收包。
- PIO 接收与当前 DMA RxBuffer 有适配差异。允许在 network 的 buffer.rs 提供最小的安全 CPU 写入接口，
  保留既有 DMA 驱动语义；按实际平台接口实现，不新增虚拟网卡或伪造网络收包。
- 保留源许可证归属：ne2k-pci 与共享 8390 文件各自声明，不整体替换成目标 MPL。

## 已经预置

完整内核 ISO、Docker/KVM、pc/i440fx + OVMF、PCI slot 5 的 `ne2k_pci`、独立 socket 网络对端。
对端在同一容器 loopback 运行，无宿主端口、TAP、外网或 root 网络配置。
`dpf_ne2k_public.rs` 和三个 case 的刺激、断言不可修改：

1. probe：真实注册、PROM MAC、能力、停止后的发送状态。
2. traffic：96 帧长度 60/61/127/256/513/1514，实际收包回调与逐字节响应验证；额外 16 帧有限 burst，
   不再追加包唤醒残留 RX。外部对端核对发送序列、长度与内容，响应做独立变换。
3. recovery：三次重复 stop/start，停机拒绝发送，恢复后真实 1514 字节往返。

最终使用同一源码/ISO 完整运行三个 case。开发中使用已有 driver_checks.check，
不需要自行写测试、网络 peer、构建脚本或新审阅报告。一个完整驱动目标可以是一个工作包。

公开结果只覆盖 QEMU RTL8029、指定流量和生命周期；不代表真实克隆硬件、ISA/PCMCIA、
全 TCP/IP 集成、动态卸载/热插拔、所有溢出/故障注入或并发交错都已验证。
