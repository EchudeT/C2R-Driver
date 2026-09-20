# Codex Job 契约

## 耗时、token 与费用统计

```bash
dpf status PROJECT
dpf status PROJECT --json
# 老运行未记录模型名时，明确指定其实际使用的模型：
dpf status PROJECT --pricing-model gpt-5.6-sol
```

每个阶段显示 `time`（累计运行墙钟时间）和 `attempts`（启动次数）；返工、失败和重验
均累计，已经通过的阶段被回退时不会丢掉旧耗时。等待用户的时间单独计入 `waiting`。
控制器停止但阶段仍是 RUNNING 的间隔会计入墙钟时间；它不是 CPU 时间。
JSON 同时提供每阶段/每次 Codex 调用的耗时、用量和全流程合计。

Codex 调用自动写入 `.dpf/codex/*.metrics.json`，记录开始/结束时间、模型、服务档位、
thread ID、调用前累计计数与结束累计计数。恢复同一对话时按差值计算本次输入和输出，
不会重复累计历史用量；缓存输入包含于输入总数，推理输出包含于输出总数，不另加一遍。
仅读取小型 metrics 文件及必要的首条 thread 事件，不把整个对话重新送给模型，
也不调用任何计费 API。

缺失计数、计数回退、缺失恢复基线均显示 `unknown_usage`；失败调用不能假定为免费。
缺失基线的下一轮也不强行归属跨轮 token，后续建立新基线后继续统计。
`unpriced` 表示无法报价的调用；`partial` 表示费用只是已知调用的小计。
历史运行的原始日志和账本不被改写；缺少模型信息时不根据当前配置猜测历史模型。

价格来源：[OpenAI 官方价格](https://developers.openai.com/api/docs/pricing)，
快照日期 2026-09-19，单位 USD / 百万 token。当前支持 GPT-6 Astra 和 GPT-5.6
Sol/Terra/Luna。例如 Sol Standard 小上下文的输入/缓存输入/输出分别为 $4/$0.40/$20。
本工作流设置 224k 自动压缩，费用统一按小上下文单价估算，显示单一 USD 金额。
224k 是压缩触发阈值而非请求长度硬上限，因此这是估算假设，不是实际账单保证；
不根据整段对话累计输入量误判长上下文。

新调用保留当时的价格估算和来源，不因以后价格表更新重写旧报价。
默认使用记录的 `service_tier`（未配置则 Standard），可用 `--pricing-tier standard|fast|flex|batch`
显式重新估算；`priority` 按官方 Fast mode 价格计算。暂不报价未收录模型、非零 cache-write
计数或未知服务档位，不擅自套用相似模型价格。金额不含地域附加费、工具费用或中转商折扣，
不代表中转商实际账单。未来调用自动记录模型，不需要工作者填写表格。

## Prompt 组成

阶段 Prompt 由 Prompt Pack 组合。默认 Pack 位于
`src/driver_port_factory/data/prompt-packs/default/`，包含：

- `manifest.json`：阶段到 Skill/reference、交付目标及可选输出 Schema 的映射；
- `job.md`：共享的职责、Skill 适配、报告和修复协议；
- `execution.md`：仅环境、实现、包装、运行和条件审查阶段加载的执行边界；
- `correction.md`：统一包装 `controller_feedback`，一次处理整批问题，保留有效工作。

自动流程不得在 Python 常量中另藏阶段目标来覆盖 Pack。`--objective` 仅作为显式调用选项。
成功自检和返工结论写入报告文件；最终聊天回复只交 `REPORT_PATH`。JSON 选择任务不需要
报告文件或结论标记。实现/运行成功使用 `DPF_SELF_REVIEW: PASS`；任一 Markdown 任务发现
缺少已通过的前置条件，可用 `DPF_REPAIR_STAGE` 加 `DPF_REVIEW: REWORK` 请求最小回退。
真正的外部阻塞用 `DPF_STATUS: BLOCKED`，不会自动要求重写 PASS。解决后由操作者执行
`dpf stage reopen PROJECT STAGE --reason '实际解决内容'`，再恢复 port；保留旧证据但不重放旧答复。

public runtime 先写好 harness，报告结尾使用 `DPF_RUN: PUBLIC_QEMU` 请求控制器执行。
控制器保存哈希绑定的 receipt 并把结果交回同一 worker；worker 检查观察和 oracle 后完成
原报告的最终自检。不为了写报告再跑一次完整套件。源代码/镜像、脚本或 harness helper
输入变化会使 receipt 失效。开发用探针及合同要求的重复运行仍保留。

报告、scratch/runtime 脚本放在 target worktree 的 `.dpf-output/`；其他阶段报告放在其 cwd。
实现快照相对冻结上游，而非 HEAD，支持本地 Git checkpoint、普通文件删除/重命名和执行位；
不支持实现 symlink。不得把错误报告路径或已提交的正确代码当作隐藏的实现返工要求。

提示词遵循 [OpenAI 官方提示指导](https://developers.openai.com/codex/prompting/) 的目标、
相关上下文、交付与边界划分；具体迁移验收仍以所载入的上游 Skill 为依据。必要上下文可保留
在已有报告中，不强制额外工作日志、重复表格或多角色自审。

渲染结果包含角色与信任边界、对应 Skill 原文、本次 artifact/evidence 清单、目标和输出约束。
组合器在每次 Job 开始时重新读取当前文件，把完整 Prompt 和所用文档摘要登记为输入证据。普通开发
允许频繁修改 Prompt Pack 和 Skill；只有正式 held-out batch 才由独立实验契约冻结选定版本。

项目可用 `dpf init --prompt-pack PATH` 选择 Pack；单次 Job 可用 `--prompt-pack PATH` 覆盖。
控制器校验 Pack 的可读结构、引用 stage 是否属于全局领域 catalog，并在渲染时进一步限制为当前
角色 DAG；它不校验自然语言必须等于某个旧模板。

### 审计修复前的离线检查记录（2026-09-20）

旧合规节点与诊断旁路已删除，对应旧测试不再保留。测试关注完成、失败路由、证据绑定和
恢复等外部行为，不锁死提示词措辞或内部实现。全量离线回归 245 项、43 项子测试通过，
用时约 161 秒，日志 `/tmp/dpf-final-flow-suite.log`。控制器集成场景使用模拟源码/模拟器
和替代模型响应，不等同于真实 NE2000 迁移通过；没有重启付费流程。

公共提示词从本轮开始的 12,681 字节缩到 6,469 字节（约 49%）；详细审查规范仅放在
条件审查目标中。未改动上游 Skill 原文。该比例不是整次输入 token 或实际费用降幅。
工作报告由控制器冻结进 CAS，恢复与返工读取报告正文，不追随可能被重写的 REPORT_PATH。
当前回退原因从已有 ledger 取出并随下一次任务传入，不增加模型表格或新审查节点。
最终收窄审查触发条件后，只复跑受影响的提示词、审查与路由场景，28 项通过；未再次运行
无关模块。包含普通已有 safe Rust 接线不启动审查模型、unsafe 边界触发一次审查、包装返工
不重做实现且复用原会话的控制器集成场景。

后续全面审计修复的变化、验证和剩余限制见 [修复记录](AUDIT_REPAIR_IMPLEMENTATION_2026-09-20.md)。
上述测试数量和包装字节属于先前快照，不代表当前版本。

## 执行模型与权限

`CodexJob` 的内部字段为：

```text
job_id, stage, actor_role, objective, prompt,
execution_root, sandbox, output_schema?, model?, thread_id?
```

调用者不能通过 CLI 指定 sandbox、可写目录或 thread ID。`CodexExecutionPolicy` 从 typed stage 和
repository manifest 推导唯一 grant：

| 阶段 | sandbox | execution root |
|---|---|---|
| 实现、产物准备、公开运行 | `workspace-write` | `work/target-working` |
| 环境、源码闭包、研究、合同及测试设计、条件独立审查 | `workspace-write` | `work/stage-work/<stage>` |
| revision/evidence acquisition | `danger-full-access` | 项目根 |
| 其他阶段及独立评测 | `read-only` | 项目根 |

`public_repair` 是条件证据收尾：无风险触发时控制器冻结工作者自检证据，不调用模型；
触发时在 `work/stage-work/public_repair` 使用独立会话，审查列出的风险和相关路径。
触发依据是 Rust 语法单元的实质变化及其与 unsafe/extern 边界的名称依赖，或工作者的明确请求
（含仓库明确要求）。分析冻结基线和当前源码，也检查未改文件中的调用者；不把同文件旧 unsafe
自动当成当前风险。注释、格式和字符串中的 unsafe 不构成风险关键字。风险上下文包含变化
scope/行号、关联 boundary 和触发依据，审查者无需自行猜测整文件为何被送审。
这是保守的语法/名称索引，不是编译器完整调用图；同名符号不强行消歧，impl 块仍作为整体。
变更单元包含宏调用、通配导入或解析错误时保守标为未解析影响；源码超过 16 MiB、依赖索引
超过 500,000 条边或有未解析 Rust symlink 时明确回退审查，不静默跳过。
触发不等于缺陷；没有触发也不是功能正确性的证明，自检和测试仍必需。
工作者自检不冒充独立审查；输出记录实际 `review_mode`。旧独立合规节点已删除，不做兼容迁移。

可写 grant 必须位于当前项目内，且不能等于或包含项目根、`.dpf`、source baseline、target baseline
或 QEMU baseline，也不能位于这些目录之下。策略在启动 Gateway 前 fail closed。CLI 不提供提权参数。

## 输出与成功门禁

Gateway 成功返回后，控制器在 `.dpf/codex/` 写入 canonical result，并把它登记为 auxiliary
`codex_job_result`；JSONL 事件存在时另登记 auxiliary `codex_event_log`。这些原始模型证据不会改变
阶段状态，也不能直接声明 `PASS`。

CLI 不提供把模型响应转换为 required output 或直接 finalize 的通用入口。所有阶段都必须由明确的
领域 adapter 解析辅助响应或工作区变更，生成 typed artifact 完整 bundle，执行逐件 validator 和
跨产物 bundle validator，再通过唯一的 `Project.finalize_stage` 原子提交。
开发者流程允许直接交付代码、脚本和 Markdown；Markdown 阶段不为不存在的跨产物校验加载上游 AST。

源码阅读优先，编译器事实按问题查询。`knowledge c-facts PROJECT --symbol NAME` 返回
`results`，不是 `matches`；`--detail calls` 展开被摘要截断的调用，`--detail cfg` 获取该函数的
编译器 CFG。source_closure 内先提交编译输入并请求 `DPF_RUN: SOURCE_ANALYSIS`，收到控制器
事实收据后在同一任务内查询导航并完成自检。未生成、损坏与真实缺证据是不同状态。
工具不要求模型消费整个 AST、复述机器索引或另填分析表格。

默认 Prompt Pack 不启用 `output_schema`。版本/证据选择返回简洁 JSON，由领域 importer
解析并校验；工作报告通过唯一的绝对 `REPORT_PATH` 交付真实 Markdown 文件，不接受聊天正文降级。
通用自定义 pack 支持声明输出 schema，但这不是默认流程已经具备的保护。

`evidence_closure` 是已实现的领域 adapter：importer 校验提议外形，并按
`codex_job_result` 的 digest+ordinal 精确绑定并执行 Python typed validator；静态 materializer 再验证
真实文件、Git blob、外部输入和完整五件套 bundle。原始/结构化 Codex 输出仍只是 auxiliary。

## Gateway

- `CodexExecGateway`：首次创建 thread，返工和进程恢复使用原 thread；
  只重新注入变化的 Skill 文档，最新输入和反馈以文件路径交接；

模型名称可按 Job 覆盖。thread ID 从项目、工作者/审查者身份、角色、模型和配置绑定的会话记录恢复，
不能由 CLI 注入。每次调用及恢复均设置 224000 tokens 自动压缩阈值；sandbox 由阶段策略确定。
开发者流程从证据研究、环境、契约与测试设计到实现/构建/公开运行共用工作会话，
目标检查和最终证据检查共用另一独立会话。每次执行仍按阶段重新应用目录与权限，
不把上一阶段的写权限带到下一阶段。不迁移旧会话，不提供 SDK 兼容入口。
省略的 Skill 文档附带原文绝对路径，压缩后可按需重读。异常中断或没有最终回复不能成为阶段结果。
环境恢复、源码编译准备及构建阶段提供依赖下载网络权限和阶段内 Cargo 缓存。
运行失败直接路由回实现者，重新经过快照、检查、包装和运行，不再额外启动修复模型。
详见 [新版对齐说明](WORKFLOW_ALIGNMENT.md)。

对 `INDEPENDENT` owner，这个 fresh-thread 行为只是必要条件。Job 还必须从未保留迁移对话的新
Codex 上下文/进程启动，并使用角色专属项目、凭据和材料挂载。共享迁移上下文或能读取候选/私测双方
材料的进程必须报告 `NON_INDEPENDENT`，不能靠再次调用 Gateway 恢复独立性。
