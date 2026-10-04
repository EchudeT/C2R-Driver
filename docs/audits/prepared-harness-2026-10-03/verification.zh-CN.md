# 自动测试入口验证

本次变更：托管基线验证后安装 smoke/public 入口，platform register_case 生成用例包装及既有清单，
生成的 smoke 与 public 捕获复用原逐用例执行器。旧冻结实验没有修改，没有启动付费模型。

## 聚焦回归

- 平台执行与新入口：14 passed in 18.86s，见 pytest-entrypoints.log。
- 新套件专项：4 passed in 7.32s，见 pytest-generated-suite.log；与上组重叠，不相加。
- 另一次早期组合回归有一个测试 fixture 未模拟 command(None) 的错误，已修正；没有将其称为全通过。

覆盖的真实边界：

1. baseline verify 后就有两个入口，但没有凭空生成设备用例。
2. 实际 shell 入口在空清单时失败；独立用例失败不会隐去其余用例的执行结果，单用例超时失败。
3. 登记只写入文件，未执行也未验收；同 ID 更新不丢失其他条目。
4. 合成 QEMU 经本地真实进程和控制器捕获；开发逐用例检查之后，生成的 smoke/public 捕获及
   implementation_smoke 门禁复用相同有效收据，不再次启动模拟执行器。
5. 修改 harness 输入后旧收据失效；真实失败结果不能被另一项通过或历史通过覆盖。
6. 分析阶段不能登记实现用例，现有自定义入口不会被自动覆盖。

这些是合成驱动/模拟 QEMU 的本地执行器与控制器回归，不是真实内核翻译、设备质量或费用证据。
自动生成的独立用例绑定自身定义及公共 harness，其他独立用例变化不导致重跑；
共享 helper 变化仍会使相关旧收据失效。专项回归最终 4 passed in 7.89s，见 pytest-reuse.log。

## 检查状态

新模块 suite.py、变更的 worker.py/service.py 及两份聚焦测试文件的 Ruff check 与 format --check 通过。
全仓 Ruff 仍报告 576 项既有问题，format --check 报告 134 个待格式化文件；未全仓重写。
该批全量 pytest：441 passed in 729.72s，见 pytest-full.log。
之后新增固定公开测试属于下一批，结果另见 public-tests-verification.zh-CN.md。
