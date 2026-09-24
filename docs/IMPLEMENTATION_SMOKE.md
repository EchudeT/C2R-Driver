# 实现阶段的功能自测

`driver_implementation` 在提交源码快照前执行最小目标功能测试。在关闭分析审查的
audit-19 中，这是第 14 步；文档中的完整流程编号为第 15 步。

工作者按冻结合同准备当前驱动镜像、`check-presence.sh` 和
`implementation-smoke.sh`，统一放在目标 worktree 的 `.dpf-output` 下。
脚本使用 `DPF_RUNTIME_ARTIFACT`，辅助输入放在 `.dpf-output/harness`，
每次执行的日志放在 `.dpf-output/qemu-runs` 的新目录中。

自测覆盖组件初始化、精确设备身份、注册、probe/binding、初始化就绪和单次适用数据操作。
NE2000 使用 `ne2k_pci`、PCI `10ec:8029`，检查一次 TX/RX 的外部载荷观察。
Asterinas 构建与 QEMU 都使用已固定的官方开发容器。总自测时限为 300 秒；
guest 的预期超时只有在全部功能断言满足后才可由脚本判为成功。

控制器先执行镜像检查，再复用公开 QEMU 的命令、容器、镜像参数和日志采集机制。
功能断言由工作者依据合同实现；程序采集执行事实，不从日志关键字推断完整功能。
控制器将命令、退出状态、执行轨迹、容器身份、镜像 hash 和日志索引保存在
`.dpf/implementation-smoke/<run-id>/receipt.json`，通过结果嵌入 implementation bundle。
失败后同一阶段工作者收到实际结果，继续归因和修复；保留失败记录。

成功结果只在源码、镜像、检查脚本和辅助输入相同时复用。报告文字变化不触发重跑。
后续 artifact preparation 可以复用已生成的镜像，完整公开 QEMU 阶段仍执行 IRQ、
边界、恢复和回归等约定测试。最小自测通过不代表完整测试集通过。
