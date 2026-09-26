# 分析到执行的上下文交接

新建 developer-evidence 项目默认使用 `analysis-handoff`。研究、契约与可选分析审查完成后，在首次 `target_framework_enablement` 调用前归档旧会话指针，生成不可变交接材料并启动新会话。框架使能、驱动实现、编译及运行修复继续使用这个会话，不在每个子阶段反复重置。

交接材料由控制器从现有状态生成，不额外调用模型。它包含当前产物的路径和摘要、阶段状态、已有执行观测、历史会话指针与日志位置。新会话先读交接及有关分析材料，按需查看原始证据；不默认重读整段历史或所有文件。源码、工作区、预算和验证记录不会被清空。

分析报告的写作指引同时要求保留关键结论的证据位置与版本、接口前提、未决问题、被否定方案及原因、下一步动作。这些内容写入原有报告，不新增 schema、验收门槛、审查调用或失败重试。历史模型判断仍是待核查的主张，不因写入报告就成为已验证事实。

## 配置与兼容性

```
dpf port run ... --context-policy analysis-handoff
dpf codex context-policy RUN --policy analysis-handoff
dpf codex context-policy RUN --policy persistent
dpf codex context-report RUN
```

- 已有项目不自动更改策略；旧项目没有策略文件时仍采用 persistent。
- `implementation-handoff` 保留兼容，在首次驱动实现前交接。
- 框架使能或实现已有调用记录时，后来启用 analysis-handoff 不打断进行中的工作。
- 修复、续接、待消费交接、没有旧线程、契约材料不完整或已启用的审查尚未完成时，保留会话，不新增阻断。
- 未启用分析审查时不要求额外审查。盲评模式保持原来的角色隔离策略。
- 自动交接记录保留在会话及账本中；进程中断后可继续消费同一交接，避免重复重置。显式重置功能仍可用于操作员指定的恢复。

这是用户选定的工程默认值，不代表已经证明端到端成本或质量优于 persistent。已有文本实验与原生会话的区别见 [实验有效性分析](NATIVE_CONTEXT_VALIDITY_2026-09-26.zh-CN.md)。
