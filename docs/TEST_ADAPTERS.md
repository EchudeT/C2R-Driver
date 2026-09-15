# 驱动测试适配层

测试迁移的稳定中心是设备行为，而不是 Linux 或目标 OS 的内部测试 API。

```text
Scenario + Oracle
       |
DeviceClassProtocol
  /             \
Linux fixture   Target fixture
  \             /
 QEMU or hardware backend
```

## 分类

- `PORTABLE`：包内容、块读写序列、串口字节流、寄存器时序和错误恢复，可直接保留逻辑；
- `ADAPTABLE`：核心断言可保留，但注册、夹具、并发或观测接口需替换；
- `SOURCE_INTERNAL`：只验证 Linux 内部对象、锁实现或测试框架，排除并记录原因；
- `UNSUPPORTED`：目标平台或 QEMU 模型没有对应能力，明确标记而不是伪造测试。

设备插件提供 scenario、stimulus、observation 和 oracle；平台插件只处理设备发现、生命周期和 OS 接口。这样 Network、Block、Serial 分别共享本类逻辑，但不被迫实现一个失真的万能驱动接口。
