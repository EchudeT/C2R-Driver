# pvpanic：预置公开断言，模型适配接口

面向新的 Linux pvpanic-pci → Asterinas、source-driver / target-kernel 任务，控制器在基线验证后
安装三个公开用例及两个运行入口。翻译模型不再编写这些测试或断言，只实现驱动、适配测试调用、
运行测试并修复具体失败。其他驱动尚未提供固定测试集合，继续使用现有用例登记接口。

## 接口和覆盖

完整定义在 [INTERFACE.md](../src/driver_port_factory/data/public-tests/pvpanic/INTERFACE.md)，
固定刺激与断言在 [cases.json](../src/driver_port_factory/data/public-tests/pvpanic/cases.json)。

| 用例 | 触发方式 | 判定 |
| --- | --- | --- |
| pvpanic-native-panic | QMP 注入 NMI，经过真实内核 panic 入口 | capability=1、初值和非法掩码；实际 GUEST_PANICKED |
| pvpanic-native-shutdown | guest poweroff -f，经过真实关机入口 | 实际 GUEST_PVSHUTDOWN，普通 SHUTDOWN 不替代 |
| pvpanic-controls-lifecycle | /proc/pvpanic 适配真实驱动操作 | 初值、零掩码、非法输入不改状态、panic/crash 掩码、移除后无事件 |

`/proc/pvpanic` 是一个轻量测试适配口，不是完整 Linux sysfs 模拟器。模型可以编写命令分发，
读写实际驱动状态并调用实际回调/移除逻辑；不能复制一套仅供测试的设备实现、制造期望值，
也不能改断言。测试接口与原生通知两类结果分开解释。

公开用例不等于工作包。三个场景可以由一个完整工作包实现和检查，不引入测试设计阶段或审阅模型。
分析阶段即可看到接口路径和 ID；模型按需读取一份短说明，无需重新研究测试框架。

## 控制器责任

- 自动登记固定用例和包装，当前包可选择相关 ID；最终入口执行完整集合。
- 定义在实验控制器快照中固定，安装时绑定定义；缺失、改写、跳过或重定向用例不能验收。
- 运行时使用原有 Docker/OVMF/KVM、制品身份与逐用例收据复用机制。
- QMP 事件计数检查实际累计次数，不能用一个正确事件掩盖额外错误事件。
- 负检查使用预先定义的 0.2 秒观察窗，原始日志完整保留。窗口之外不作保证。

这是面向正常开发误改的完整性检查，不声称同一用户权限下的恶意进程隔离。
模型仍能做临时探索，但探索不会自动替换固定验收集合。契约正文引用现成用例 ID 即可。

## 研究边界

这些是开发者可见的 NEW_MIGRATION_TEST，依据本次冻结 Linux pvpanic 源码和 QEMU 事件语义预先编写。
不是独立盲测、未见任务，也不是证明完整源义务已覆盖。没有缩减既定 source-driver 范围。

当前未覆盖多设备并发、锁竞争、分配/注册失败注入、真实 kexec、PCI 热拔插及实机稳定性。
模型仍需检查源码中的相关义务并如实报告限制；未穷尽测试不自动派生无限补测或新工作包。

测试接口可能增加少量接入代码，是否比模型自主设计测试节省总费用，需要新实验测量。
此次实验同时更改工作包提示、基础设施和公开测试，不能将费用变化全部归因于某一项。
