# 托管工具直接调用

Factory 配置 `driver_checks` 时，向 Codex 0.160.1 传入：

```text
-c 'features.code_mode.direct_only_tool_namespaces=["mcp__driver_checks"]'
```

该命名空间只通过直接工具调用执行，不再进入 code mode 的外层 `exec`。
现有 MCP 实现已经等待构建、测试或诊断完成，返回最终结果及日志位置；此配置
针对外层提前返回后模型反复调用 `wait` 的开销。普通代码读取、编辑及其他工具
仍按原方式执行。首次调用与续跑都设置此参数；没有托管工具的任务不设置。

不修改提示词、B/C/R/P、工作包选择、持续会话、测试断言或最终验收。
沿用工具超时、实验时限和进程取消清理；直接调用不意味着无限等待。
源码日志和进度仍可供宿主监控读取，不要求模型轮询。

依据：[官方配置说明](https://developers.openai.com/codex/config-reference/)。
网关测试是离线参数边界检查；本机 Codex 工具暴露检查使用本地模拟服务，不调用
付费模型，不代表驱动功能或成本已通过真实实验验证。
历史冻结检查点不追溯修改；新实验使用独立提交和 trial ID。
