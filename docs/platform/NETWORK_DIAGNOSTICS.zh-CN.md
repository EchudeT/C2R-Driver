# 按需网络抓包与 QEMU trace

真实流量失败时，工作者可调用 `driver_checks.network_diagnostics`，指定一个现有 native
网络测例。工具使用当前已构建的 ISO，复用原测例的 guest 操作、外部 peer、超时和断言，
只增加 QEMU 观察参数；不改驱动、不重建、不改变网络后端。

```json
{"case":"e1000e-recovery","trace_events":["e1000e_rx_written_to_guest"]}
```

`case` 使用原始 native case 名称；多个 netdev 时可选 `netdev`。trace 不必提供；
设备事件必须是固定 QEMU `-trace help` 中的实际名称，不能使用全设备通配符。
PCAP 使用 QEMU `filter-dump`，不依赖镜像安装 tcpdump。每帧最多保存 256 字节，
用于观察报文头，不保证完整载荷和校验和验证。PCAP、串口或 QEMU stderr 超过
16 MiB 时，在下一次事件循环检查中停止诊断；不作严格零超量的存储保证。

返回 ARP/TCP/UDP 数量、前 24 帧地址/端口/TCP flags、最后 4 KiB QEMU trace 及完整
日志路径。证据保存在 `.dpf-output/network-diagnostics/`。没有流量或没有 trace
不能直接推导驱动故障；还应核对刺激是否执行、trace 是否覆盖相关设备路径。

这次重放单独记为诊断，不发布最终 PASS、不更新开发检查的保留成绩。完成修复后仍走
原公开验收。工具不是每包或每次失败的强制步骤。

## Baseline 边界

诊断封装属于 Factory 方法。Codex 不获得这个工具、操作手册或专门的诊断提示词。
两种方法保留相同镜像及 QEMU；Codex 可以自主发现和使用原生抓包/trace 能力。
PCAP 可由操作者离线用 Wireshark 或 tcpdump 阅读。

## 验证状态

新增诊断及平台工具离线检查共 14 项通过，覆盖 ARP/TCP 解码、网络配置保留、缺失 trace
提示、失败测例证据留存及日志上限。扩展 native 检查共 21 项中 20 项通过；剩余一项
`test_native_dpf_uses_shared_image_and_exact_final_artifact` 的 fixture 缺少 `task`，在旧
分支复现同样失败。固定镜像的无 guest QEMU 启动检查成功启用 trace 并创建 24 字节 PCAP
文件头。它们不等于真实驱动诊断效果
验证，尚未测量工具能否解决当前 E1000e 的流量故障。不热更新现有付费实验。
