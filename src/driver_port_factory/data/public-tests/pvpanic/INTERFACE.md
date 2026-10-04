# pvpanic 固定公开测试接口（v1）

这是开发者可见的 NEW_MIGRATION_TEST，依据冻结 Linux pvpanic.c 与 QEMU pvpanic 事件语义编写。
不是独立盲测，不是完整语义覆盖，也不缩减 source-driver / target-kernel 的交付范围。

## 模型任务

实现真实 pvpanic-pci 驱动及原生 panic、poweroff 通知接入。用一个轻量测试入口
`/proc/pvpanic` 适配下述调用；入口不能复制 MMIO、掩码或移除算法，必须调用真实驱动实现。
该入口是测试适配，并不证明 Linux sysfs ABI 在目标上原样存在。可参考目标 procfs 的
`uptime.rs`（读取）、`sys/kernel/tainted.rs`（写入）及 `root.rs` 的注册方式，按需读源码。
不需要编写测试脚本、断言、成功标记或新的测试框架。不要拆成额外工作包。

读取返回一行 `capability=<hex> events=<hex>`，小写、无 0x 前缀、一个空格分隔，末尾换行。
这些数值来自当前真实设备实例的状态，不是测试期望常量。
写入一行（允许末尾换行）：

- `events <hex>`：调用实际事件掩码更新接口；非法文本、u32 溢出、超出 capability 的位
  必须返回错误，保留旧掩码。实际接口应承接源控制义务，测试适配只负责命令分发。
- `notify panic`：调用真实 panic 通知回调的无 crash-loaded 分支。
- `notify crash`：调用相同回调的 crash-loaded 分支，不实现目标 kexec 子系统。
- `notify shutdown`：调用真实 shutdown 回调；测试中的这一调用只检查被禁用/移除时无事件。
- `remove`：调用实际驱动清理/移除入口，停止设备参与后续通知。测试不要求 PCI 热拔插框架。

notify/remove 成功返回写入长度；被掩码抑制或已经没有设备时，通知仍可正常返回。
入口只给 root 写权限。正常冷启动不调用测试操作；原生 panic、poweroff 测试不经 notify 命令。
不要实现 guest 内通过/失败判定，测试断言由宿主的固定用例执行。

## 三个公开场景

1. `pvpanic-native-panic`：capability=1、初始 events=1、拒绝不支持的事件位，
   从 QMP 注入 NMI 触发真实内核 panic；宿主必须收到一次 GUEST_PANICKED。
2. `pvpanic-native-shutdown`：capability=4，guest poweroff -f 触发实际关机路径；
   宿主必须收到一次 GUEST_PVSHUTDOWN，普通 QEMU SHUTDOWN 不能代替它。
3. `pvpanic-controls-lifecycle`：capability=7，检查初值、清零、非法输入不改状态、
   panic/crash 互斥掩码、移除后不再通知。宿主按步骤核对真实 QMP 事件累计次数，
   不仅检查某个成功日志。每个负断言有固定 0.2 秒观察窗，并非无限时间保证。

公开集合预先安装到 experiments.json。实现中调用 driver_checks.check cases=[需要的 ID]，
完成后省略 cases 运行全套；不要修改用例或重写两个总入口。契约正文可以引用上述 ID。
额外调试可以 run_case，但无需为这些公开场景重新编写测试或登记包装。

## 覆盖限制

单设备、单 CPU QEMU；不覆盖多设备并发、锁竞争、分配/注册失败注入、真实 kexec、
目标 PCI 热拔插或实机稳定性。接口调用证明相应驱动逻辑，不证明这些源机制全部在目标存在。
原生入口的两项检查与接口检查分别报告。完整源义务仍需实现阶段源码核对并明确未验证项；
这些限制不自动派生额外模型回合。发现具体违反义务的路径才修复。
