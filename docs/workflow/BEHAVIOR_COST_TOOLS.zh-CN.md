# 逐行为实现：工作包与通用工具优化

本次修改针对 v1 的实现开销，借鉴 v2 的小工作包和预置执行基础设施，保留 v1 的联合分析、
跨驱动知识积累、原生目标接入和公开验收。适用于新启动并冻结当前代码的任务；不热更新现有实验。

## 改了什么

| 浪费来源 | 实现 | 验证边界 |
| --- | --- | --- |
| 按注册、读取、保存等代码步骤拆分，反复构建和交回 | 联合分析按可观察结果定义 B 小节；必要初始化、失败和清理属于同一行为 | 简单驱动可以只有一个工作包，不预设数量。控制器每轮只选择一个行为，done 最多推进一个 |
| 同一段分析在 current 和 route_context 中重复 | 提示渲染器将完全相同的正文放入 passages 一次，用 D1 等短引用关联 | 不做语义摘要，不改计划 ID、依赖、契约或进度；不同正文都保留 |
| 每个驱动手工补 Cargo、组件表和内核引用 | 可选 platform scaffold 生成目标组件的机械接入 | 仅提供骨架，不生成设备实现、不声称初始化顺序正确或已通过 |
| 为简单断言手写 QMP/串口脚本和 JSON 文件 | run_case 支持内联对象，增加事件字段、QMP 返回值匹配 | 保存真实用例，经同一执行器运行、记录。断言和设备刺激仍来自当前义务 |

当前行为必须完成其必要的区分性检查，再交回 done。能在一次启动中完成的相关断言可以一起运行，
不能把当前行为的首次正确性检查留到最终验收。最终验收继续检查整个交付。

## 工作包与行为边界

对照 v2 的 analysis_document.document_packet、planning.build_plan/select_next/work_packet：
R（粗路径）、B（可调度工作包）、C（源义务）分开。粗路径中的注册、读写、通知和清理步骤不自动
产生调度项；一个 B 可以关联多个 C、多个源码函数和多个测试场景。

简单且内聚的驱动使用一个完整工作包即可，轮内一起完成实现、按需查询、检查和修复。
只有工作量或独立目标确实适合分开时才拆；“可独立观察”本身不要求另起一轮。
不预设 pvpanic 必须三个包，不按“初始化、通知、控制、锁、清理”套模板，也不设拆分理由表或审阅门禁。

v1 原调度核心已经支持一包多契约和轮内多次工具操作，不需要新的状态机或自动合并启发式。
本次修改联合分析/执行提示、控制器工作包说明，并用真实控制器的离线回归验证：
两个契约只有一个实现单元，done 之后才进入交付检查；continue 保留该单元和同一会话。
保留既有多包调度能力，控制器不擅自把已声明的独立目标合并。

v1 最后仍有一次交付材料整理调用及程序验收，不能声称模型调用总数已经与 v2 相同。
后续已补齐[自动测试入口](../platform/PREPARED_TEST_ENTRYPOINTS.zh-CN.md)：登记一次设备用例，开发检查与
最终验收复用统一清单和有效收据，不要求模型再编写 implementation-smoke.sh/public-qemu.sh。
这里对齐的是完整工作目标和轮内执行方式；语义义务和最终验收范围没有减少。

行为提示中的 current 与 route_context 保留原结构和关联。正文例如：

```json
{"current":{"id":"B1","outcome":{"text_ref":"D1"}},
 "passages":{"D1":"此处是未经摘要的原正文"}}
```

D 引用由控制器生成，模型不用填写或维护。完整分析文件仍可按需读取。
活动行为回合只保留整份交付摘要的来源导航；完整 delivery-task.md 改为按需引用。
当前关联契约、前提和具体修复反馈保持可见。最终交付恢复完整交付说明。
去重只发生在渲染副本，不改变规划或验收状态，也不把 route 步骤变成工作单元。

## 可选组件脚手架

通过已有 driver_checks.platform 使用：

```json
{"action":"scaffold","package":"aster-new-device","template":"aster-console",
 "dependencies":["ostd"],"owner_source":"kernel/core/src/init.rs"}
```

这里的名字只是接口示例。模型根据当前目标源码选一个使用 component 初始化的已有组件，
指定当前行为需要的 workspace 依赖。控制器：

1. 从当前目标的 workspace manifest 定位模板相邻目录与指定 owner crate。
2. 补 workspace 成员、需要的 default-members、workspace 依赖、Components.toml、owner 依赖。
3. 在指定已存在的 Rust 源码中加入实际 crate 引用，并创建最小 no_std crate。
4. 返回精确 diff 和 SCAFFOLDED_NOT_IMPLEMENTED。初始化函数包含 todo!，必须由工作模型实现。

该工具不复制模板驱动代码，不内置 PCI ID、寄存器、MMIO 或测试答案。
适配器目前面向 Asterinas 的显式 workspace 数组、继承 edition/lints 和组件表布局；
不宣称兼容所有 Rust 平台。格式不支持时返回具体错误，由模型在当前行为内按需查看、实现接入。
已有目标文件不会被覆盖。写入前检查输入，写失败时撤回本次已写文件及新建目录；不回滚其他工作。
分析阶段不能调用此写入操作；组件接入不是一个新的宏观阶段。
真正的引用生效、初始化顺序和设备行为仍须在当前行为中检查。

## 内联用例与断言

```json
{"action":"run_case","case":{"devices":["<设备及实际参数>"],"timeout_seconds":60,
 "steps":[
   {"assert_boot_log":"<当前行为要求的启动输出>"},
   {"qmp":{"execute":"<实际刺激命令>","arguments":{}}},
   {"expect_event":{"event":"<预期事件>","data":{"<字段>":"<预期值>"}}},
   {"qmp_assert":{"execute":"query-status","match":{"running":true}}}
 ]}}
```

示例是接口说明，不是可直接作为驱动验收的测试答案。

- 内联对象经校验后保存到 .dpf-output/harness/case-*.json，成功结果返回路径，可在最终 harness 复用。
- 原有 JSON 路径入口继续可用；两种输入都调用相同的平台执行、身份校验与原始记录流程。
- qmp_assert 比较命令实际返回内容；expect_event 比较事件名称及要求的字段。
  对象按指定字段递归匹配，列表和值要求相同类型及内容，true 不当作 1。
- wait_event 与 expect_event 共用消费记录，同一事件不能通过两次等待。
  它们可以消费本次启动中先前到达、尚未消费的事件，并不自动证明事件发生在最近一次刺激之后；
  用例须据真实协议安排刺激和等待次序。
- 原有 qmp 只执行命令；成功响应本身不验证返回字段。负向检查需包含适当观察窗口。
- assert_boot_log、guest_assert、原记录正向日志检查沿用现有边界，参见
  [启动日志断言](../platform/BOOT_LOG_CHECKS.zh-CN.md)。失败不被改写为成功，源码变化后的制品检查继续有效。

工具等待执行结束，无需模型轮询。环境继续使用冻结 OVMF/KVM 路线，不自动换固件、镜像或加速器。
所有工具按需使用，不增加固定检查轮数、reviewer 或每行为报告。

## 与 v2 实验的对照边界

v2 的 pvpanic-pci-luna-20260930-09 用一次分析调用和一次实现调用完成其公开验收；
这证明实现工作不需要固定拆成多个调用。另一次 pvpanic-pci-luna-20261001-04 在其公开回调/QMP 范围内用 3 次模型调用完成，耗时约 8.72 分钟，
其组件、测试和 QMP 基础设施事先已提供。v1 本次实验包含更完整的目标接入与测试编写，模型也不同。
不能把这两个结果直接写成相同任务、相同质量下的费用比。本次只借鉴可迁移的机制：
以行为组织工作、去掉重复材料、由平台执行器承担机械接入与通用传输。

历史 B2 提示的离线重放中，reference_material JSON 从 25,296 字节降到 21,663 字节，减少 3,633
字节（14.36%）。这只量化正文投影，不代表总 token、输出、调用次数或美元成本减少相同比例。
记录、验证范围与限制见 [本次验证](../audits/behavior-cost-2026-10-03/verification.zh-CN.md)。

## 提示词入口

- [analysis-task.md](../../src/driver_port_factory/data/prompt-packs/default/analysis-task.md)：行为切分与联合分析。
- [behavior-job.md](../../src/driver_port_factory/data/prompt-packs/default/behavior-job.md)：当前回合责任与交回。
- [behavior-execution.md](../../src/driver_port_factory/data/prompt-packs/default/behavior-execution.md)：当轮检查及工具用法。
- [component-integration.md](../../src/driver_port_factory/data/component-integration.md)：按需组件接入指导。

费用下降、真实模型是否采用这些工具、行为划分是否改善，仍需新的完整翻译实验验证。
本轮验证没有启动付费模型，也没有修改 07 实验的冻结计划。该实验已在累计估算 $10.0386 时因预算停止。
