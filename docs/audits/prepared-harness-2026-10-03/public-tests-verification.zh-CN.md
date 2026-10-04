# 固定公开测试验证

- 全量离线测试：446 passed in 739.66s (0:12:19)，见 pytest-public-full.log。
- 聚焦回归：30 passed in 46.45s，见 pytest-public-focused.log；与全量重叠，不相加。
- 六份新增/变更的规范格式 Python 文件 Ruff check、format --check 通过。
- 全仓 Ruff：576 项既有问题；format --check：134 个既有文件需要格式化。没有全仓重写。
- git diff --check 通过。

这些测试使用真实本地控制器/进程和模拟 QEMU，检验固定用例缺失/改写拒绝、定义变化拒绝、
精确事件计数对漏发与重复事件的拒绝、非对应驱动/子集不误启用固定集合，以及原有套件复用。
不是真实驱动翻译正确性证明。

此外实际检查了指定 Docker 镜像内 OVMF 文件、Cargo 和 QEMU 10.2.1 的存在。此预检没有启动
内核，不冒称 baseline boot 或新驱动通过。新实验由正常环境阶段完成基线构建与启动。

新实验 pvpanic-route-sol-medium-20261003-08 使用全新模型会话、相同知识库种子与固定镜像，
gpt-5.6-sol medium，10 美元调用间预算；冻结公开测试后模型只适配接口。启动与最终结果见
实验目录的 launch.json、experiment.json、controller.log。本验证记录不是实验结果。
