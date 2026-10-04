# 路线联合分析重构验证

日期：2026-10-03。分支：`feature/route-guided-v1`。
这是当前工作树的离线验证记录，不是已提交版本或真实驱动实验结果。

## 已完成的实现

- 联合分析只保留一份 Markdown 正文及路线引用索引；契约检查点绑定相同正文，不再单独调用模型。
- 粗路径与行为分开，分析行为直接进入控制器调度，每轮只推进一个完整行为。
- 源码足以回答时零探针；有设计关键缺口时通过固定执行器运行对应问题的最小探针。
- 托管环境阶段直接绑定基线构建、启动收据，不再强制模型编写额外设备探针。
- 普通源码修改、补充证据不重置行为；明确路线修订只影响相关行为及依赖。同一目标的路线修正可在原回合完成。
- 有依据的分析经验可选择性进入共享知识库，后续任务可检索；当前任务的查询快照不被改写，发布失败不阻断交付。
- 两个模型审查默认关闭；实际构建、公开运行及配置的固定断言验收保留。

## 执行结果

| 检查 | 结果 |
| --- | --- |
| `.venv/bin/pytest --tb=short` | 413 passed，689.44 秒，退出码 0 |
| 路线、平台工具和冻结提交等针对性回归 | 25 passed，48.78 秒，退出码 0 |
| 最后提示词修改后的 `tests/test_prompt_contracts.py`、`tests/test_analysis_boundary.py` | 通过，退出码 0 |
| 本次核心模块及测试的 `ruff check` | 通过 |
| 同一范围 `ruff format --check` | 21 个文件均符合格式 |
| `git diff --check` | 通过 |
| 全库 `ruff check .` | 未通过，576 个问题 |
| 全库 `ruff format --check .` | 未通过，134 个文件待格式化，379 个已符合格式 |

完整回归启动后增加/调整的同回合路线修正、问题正文传递、普通源码编辑与工具入口由
上述 25 项针对性回归覆盖；不把两组数量相加当作不同测试总数。
全库在本次重构前已有大量静态检查问题，本次没有批量改写无关文件，也不声称全库静态检查通过。

21 个文件的静态检查范围：

```text
src/driver_port_factory/migration/route*.py
src/driver_port_factory/migration/{behavior,validation,implementation,completion_audit}.py
src/driver_port_factory/knowledge/route_learning.py
src/driver_port_factory/codex/check_mcp.py
src/driver_port_factory/environment/{bootstrap,validation,context}.py
src/driver_port_factory/platform/worker.py
src/driver_port_factory/core/phases.py
src/driver_port_factory/orchestration/migration.py
tests/{test_route_guidance,target_support,test_optional_reviews,test_target_knowledge_quality,test_environment_bootstrap}.py
```

完整回归和针对性回归的原始终端输出保存在本目录的 `pytest-full.log`、
`pytest-boundaries.log`、`pytest-prompts.log`。

## 证据限制

用例主要使用合成仓库、控制器 fixture 和模拟平台执行器。探针用例运行本地 shell 子进程，
并未验证真实 Docker/QEMU 的设备语义。它们验证调度、来源绑定、失败保留和知识积累接口，
不能证明模型会正确识别全部源义务，也不能证明迁移后的 pvpanic 正确或达到费用目标。

上述离线验证未启动付费模型实验，未修改旧冻结实验。真实效果仍须在新的 pvpanic 工作区测量：
完整公开验收结果、总费用与时间、各阶段调用和返工原因，以及模型实际检索/复用知识的记录。
历史平台成功记录不当作本版完整翻译成功。

## 后续实验启动

完成验证后，应用户要求于 2026-10-03 17:51:40（Asia/Shanghai）启动
`experiments/pvpanic-route-sol-medium-20261003-07`。独立控制器快照、私有 Python 运行时和
全新模型会话；`gpt-5.6-sol` medium，沿用第 06 次实验的固定版本、共享库种子及 OVMF/KVM。
不复制旧会话或旧阶段状态。两个模型审查关闭，调用前累计费用检查为 10 美元，
30 分钟是工程目标而非隐藏超时。单次调用仍可能超过剩余额度。

启动核对显示控制器 ACTIVE、01–05 PASS、06 repository_acquisition RUNNING。
这仅证明实验已启动，不是翻译结果；运行中只读观察，不调整规则或人工介入实现。
配置、控制器文件摘要、启动 PID 和日志分别保存在实验目录的
`experiment.json`、`launch.json` 和 `controller.log`。
