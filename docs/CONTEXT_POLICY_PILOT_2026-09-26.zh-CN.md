# 上下文策略实现与首轮诊断回放实验

本轮在 `feat/evidence-aware-cost-control` 上实现可选策略与只读统计，并在用户授权的 20 美元总预算内完成 6 次真实模型调用。已知公开费率估算合计 **$0.5507184**；没有查询中转服务账单，不能将估算写成实际扣款。没有恢复或修改原 e1000 运行，也没有执行新的驱动构建或 QEMU。

## 实现范围

默认 `persistent`，维持现有持久会话及原生压缩。可选 `implementation-handoff` 只在首次实现调用之前、契约及测试矩阵已冻结、已有 worker 对话时重置一次；它没有驱动名称分支。

以下情况保留当前会话：已经有实现调用记录、显式 continuation、checker recovery、执行观察自查、repair feedback、缺少冻结契约、已经是新会话、交接尚未消费、其他阶段。重启与正常修复不会重新触发边界重置。规则不满足只记录 preserve 原因，不增加验收失败，也不额外调用模型。手动 reset-session 仍可处理旧诊断被推翻等需要人工判断的情况。

配置保存在运行的 `.dpf/codex/context-policy.json`，修改写入事件账本。省略选项保留既有配置；历史无配置运行默认 persistent。配置和重置都使用已有控制器锁。仅 developer-evidence 可启用自动交接，保持盲测角色边界。

交接复用上一轮 CAS 机制，保留 scope、阶段、产物引用、报告及最后 continuation；报告仍是待核对主张，不自动成为事实。**当前交接尚未结构化保存“被推翻的旧假设”**，也没有自动检测归因错误。不要把这项实现称为最优记忆管理。

```sh
# 新运行：在既有参数后添加
scripts/run-experiment.sh --workspace /path/to/new-run --driver-name DRIVER \
  --context-policy implementation-handoff

# 已有运行：仅设置策略，不启动模型
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli codex context-policy \
  /path/to/run --policy implementation-handoff --reason '开发集对照实验'

# 查看或生成描述性比较；不调用模型，不修改运行
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli codex context-report \
  /path/to/run-a --compare /path/to/run-b --stage driver_implementation
```

`resume-experiment.sh` 也支持 `--context-policy`。对于已经执行过 implementation 的旧 e1000，启用策略不会自动重置；需要时使用显式 reset-session，并明确记录人为干预。

每次调用记录策略版本、决策理由、context action、epoch、handoff、session identity。报告按策略和 epoch 汇总费用、cached/input/output、调用时间、重复不变观察，同时列出 scope、revision、契约摘要、模型、评审开关和 prompt identity。未知 usage 不补零；旧调用策略标为 unrecorded，不倒推。历史字节指纹不等于语义失败；遗漏义务与重复调查指标仍是 null，需人工或共同 oracle 评估。对照输出始终是 descriptive_only，不因身份字段相同而认证公平实验。

## 实验设计与边界

实验目录：[context-replay-2026-09-26](experiments/context-replay-2026-09-26/)。调用前冻结了 [protocol.json](experiments/context-replay-2026-09-26/protocol.json)、材料哈希、顺序和 [rubric.json](experiments/context-replay-2026-09-26/rubric.json)。调用后人工逐条评阅，结果在 [assessment.json](experiments/context-replay-2026-09-26/assessment.json)。评阅者知道条件，不是独立盲测。

两组都使用同一 e1000 失败 checkpoint 的当前源代码、契约、目标平台研究、框架报告、环境路线、runtime artifact、执行脚本和最新 receipt；使用原运行模型 `gpt-5.6-sol`，medium reasoning，禁用工具。历史组额外获得 11 个旧 receipts 和最后 worker 报告；交接组替换为简短 scope/阶段状态。每组 3 次，配对顺序交替。输入材料选择本身是实验处理。

这不是原 Codex thread 的 resume 对照：不复现隐藏推理、原生压缩、历史缓存和实际工具检索。因此也没有直接评估生产实现的 CAS 交接策略——生产交接仍保留旧报告引用。本轮仅是低成本诊断回放 pilot，不能证明真实修复、完整迁移、跨设备泛化或默认策略应该切换。

## 观察结果

| 指标 | 事实交接 | 历史回放 |
|---|---:|---:|
| 调用数 | 3 | 3 |
| 输入 token 合计 | 31,761 | 98,775 |
| cached input 合计 | 9,984 | 32,512 |
| 输出 token 合计 | 4,498 | 4,580 |
| 公开费率估算合计 | $0.1810616 | $0.3696568 |
| 单调用时长中位数 | 50.120 秒 | 92.481 秒 |
| 人工诊断 rubric 平均分 | 6/8 | 7/8 |

在此样本中，交接组输入减少约 67.8%，合计估算费用低约 51.0%。这些是回放描述值，不是生产流程节省比例；样本太少且只来自一个开发驱动，不作显著性或质量等价结论。

两组都识别了 Loopback 外壳、缺少真实数据路径、marker 制品与脚本 PASS 不能证明功能。历史组三次都明确纠正了“container QEMU 不可观察”的旧诊断；交接组没有同样明确地结合 runtime_bound 纠正它。该项评分受到材料差异影响：交接组根本没有获得旧 worker 归因，不能据此断言推理能力下降。

更值得关注：**六次都没有明确核实所选设备的中断模式是否支持建议的 MSI-X 路线**。一般性的“IRQ 语义需核对”不能替代设备与目标平台适配证据。新会话没有解决原始材料中的错误锚定，交接组部分回答仍建议 IrqLine/MSI-X。

缓存也改变成本排序：第二对中，历史组缓存命中后的估算为 $0.0455968，随后交接组未命中缓存为 $0.073948。上下文更短不保证每次更便宜。不能用理论未缓存费用取代真实 usage。

## 实验执行器与计费限制

[scripts/context-pilot.py](../scripts/context-pilot.py) 默认 dry-run。显式执行使用现有配置中的 Responses provider 和环境凭据，不在产物中写凭据。每次预留按输入 UTF-8 字节、输出上限、2 倍标准费率和零缓存优惠计算；本组预留 $4.644624。失败或不完整调用保留预留，不自动重试。脚本不是整个 port 工作流的美元硬预算器。

实测发现中转的 terminal response.output 为空，但 streamed output_text.delta 有完整答案；已保留原事件并重建 answer 文件。请求发送了 max_output_tokens=4096，但返回 max_output_tokens=null，不能确认中转执行了该限制。六次实际输出均低于请求上限，没有发生已知超额；发现后执行器增加了“上限未确认、usage 未知或响应不完整则停止下一次调用”的保护。这是实验执行器保护，不是驱动验收门禁。

请求/响应/events/metrics 都按调用保存。公开费率沿用仓库计价版本，未核对中转价差或真实账单。已停止追加付费实验，不为用满预算重复采样。

## 下一步

1. 默认继续复用上下文，交接策略只在开发集 opt-in。
2. 改进交接内容时保留短小的“已否定假设→反证引用”，连同冻结义务与当前原始证据；不要让模型每轮补写大报告。
3. 先核实设备中断模式、DMA 和网络接口的实际适配证据，再对可执行 checkpoint 做真实续调配对。20 美元本轮授权不代表应当立刻展开不可控的全迁移。
4. 后续跨设备实验固定功能范围、环境、模型、压缩与缓存策略，记录全部失败和人工操作，用共同功能 oracle 判断完成质量。

验证：新增策略回归覆盖默认行为、非边界、首次交接、跨重启重试、中断、迟到 opt-in、缺契约、未知计量和 CLI。最终完整回归 **84 passed（86.70 秒）**，包含实验执行器预算/无重试/流式答案测试。新增模块、脚本、测试及修改后的 context_reset Ruff 通过，git diff --check 和 shell 语法检查通过。未声称全仓既有 lint 已清理。

后续研究已增加 controller observation 的持久化与交接前后变化、缓存成本敏感性统计，并核对设备实际中断机制；见 [后续优化研究](WORKFLOW_OPTIMIZATION_FOLLOWUP_2026-09-26.zh-CN.md)。历史 pilot 输入与原有评阅结果未修改。
