# evbug Luna medium 实验准备

本次使用 v1 driver-port-factory 当前控制器，Linux evbug → Asterinas，模型 gpt-5.6-luna/medium。
源版本固定6d4a0f4ea72319c9a37c1a7191695467006dd272；较新的本地Linux版本已移除evbug。
目标沿用已验证Docker构建路线的d924a9635a66c7c3bb43e563eaafa2c61d6ee9d5，OVMF/KVM。

控制器预置三个公开场景（events/lifecycle/boundaries）。固定Rust刺激/断言运行于完整
Asterinas输入子系统，宿主读取实际串口，核对事件值、顺序、连接和移除诊断。
模型仅实现处理器及/proc调用接口，不改断言。设备为合成InputDevice，没有物理输入/IRQ覆盖；
不声称覆盖源全部事件类别、失败注入或并发。方案借鉴v2公开生命周期fixture，没有导入旧翻译。
与v2的组件ktest不同，本次走v1完整内核构建/启动，费用与时长不宜直接比较。

模型审查关闭，粗路线不强制拆工作包。共享库读取启动快照、成功后写回公共库；新模型home不含旧会话。
最终对同一代码/产物完整运行三个固定case，临时路径不导致重跑。启动后只观察，不中途修改规则。
费用采用已有估算费率，10美元为调用间预算检查，不是精确账单硬上限。

准备检查：7项离线公开测试/日志断言检查通过，未运行翻译模型；Docker内格式化固定Rust fixture。
真实驱动结果以新实验收据为准，当前准备记录不代表翻译通过。
