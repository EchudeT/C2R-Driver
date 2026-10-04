# evbug 固定公开测试接口

Linux drivers/input/evbug.c → Asterinas input handler。目标是实际内核输入子系统集成。
测试在完整 Asterinas QEMU 内核中通过真实 register_device/submit_events/drop 调用注入合成设备事件，
不是物理键盘/IRQ验证，也不是独立盲测。公开测试仅覆盖目标已有 SYN/KEY/REL 表示；
源端其他事件种类、分配失败和并发语义仍需在分析中明确兼容性及验证限制，不能声称全部覆盖。

## 模型负责的接口

1. 在 `kernel/core/comps/input/src/evbug.rs` 实现真实输入处理器；`pub fn register()`
   返回真实注册令牌（使用目标类型）。令牌存活时连接已有和后注册设备；drop 注销。
   采用目标 `InputHandlerClass`/`InputHandler` 及生命周期。不要编写新的输入框架。
2. 在输入crate的lib.rs导出 `pub mod evbug; pub mod dpf_evbug_public;`。
   控制器已安装后者的完整测试源码，不能修改。输入组件照常初始化，测试调用时不另留
   第二个evbug注册令牌；此输入处理器的启停入口是 register/令牌drop，不要求动态模块加载。
3. 提供 root 可写 `/proc/evbug_test`。写入 `events`、`lifecycle` 或 `boundaries`（可带换行）
   仅转调 `aster_input::dpf_evbug_public::run(command)`，成功返回写入长度，错误返回写入错误。
   入口不生成日志期望、成功标记或通过判定。可参考现有procfs读写文件的接入方式。
4. 保留可见的源格式诊断：
   `evbug: Event. Dev: <name>, Type: <unsigned>, Code: <unsigned>, Value: <signed>`
   `evbug: Connected device: <name> ...`、`evbug: Disconnected device: <name>`。
   此版本用目标InputDevice.name作为身份映射，在报告说明与Linux dev_name的区别。
   日志要在固定构建的LOG_LEVEL=info可观测；测试不要求完整日志前缀相同。

## 控制器已经提供

- `dpf_evbug_public.rs`：真实输入框架上的刺激、处理器计数及注册令牌释放断言。
- 三个固定case：事件值与顺序；已有/后来设备连接、设备移除与处理器注销；
  i32极值、空批次、非默认SYN code、注销后静默及再次注册。
- 宿主逐项解析实际串口日志并核对精确有序事件，重复、缺失、注销后多余输出均失败。
- 完整源码范围不由测试穷尽。正常路径不新增ktest、mock、故障注入或测试报告回合。

实现调用 driver_checks.check 使用已登记集合，最终验收在同一最终版本完整执行三个case。
无需自己生成断言、测试脚本、成功标记或总测试入口；必要测试接口属于同一驱动工作包。
