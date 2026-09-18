# Codex Job 契约

## Prompt 组成

阶段 Prompt 由 Prompt Pack 组合。默认 Pack 位于
`src/driver_port_factory/data/prompt-packs/default/`，包含：

- `manifest.json`：阶段到 Skill/reference、交付目标及可选输出 Schema 的映射；
- `job.md`：可自由调整的外层提示词，使用 `{{job_json}}` 和 `{{skill_documents}}` 插槽。

渲染结果包含角色与信任边界、对应 Skill 原文、本次 artifact/evidence 清单、目标和输出约束。
组合器在每次 Job 开始时重新读取当前文件，把完整 Prompt 和所用文档摘要登记为输入证据。普通开发
允许频繁修改 Prompt Pack 和 Skill；只有正式 held-out batch 才由独立实验契约冻结选定版本。

项目可用 `dpf init --prompt-pack PATH` 选择 Pack；单次 Job 可用 `--prompt-pack PATH` 覆盖。
控制器校验 Pack 的可读结构、引用 stage 是否属于全局领域 catalog，并在渲染时进一步限制为当前
角色 DAG；它不校验自然语言必须等于某个旧模板。

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
| 环境、源码闭包、研究、合同、测试设计、目标检查 | `workspace-write` | `work/stage-work/<stage>`，合同与测试共用目录 |
| revision/evidence acquisition | `danger-full-access` | 项目根 |
| 其他阶段及独立评测 | `read-only` | 项目根 |

兼容阶段名 `public_repair` 的模型任务是最终运行证据复核，共用 `target_compliance`
检查目录和会话；运行失败时仅由控制器路由回原实现者。

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

调用方不能传入任意 Schema。Prompt Pack 为结构化阶段声明唯一默认输出 Schema，控制器把它自动交给
`codex exec --output-schema`，并再次确认响应为 JSON。领域 importer 继续执行 typed parser 与跨记录
不变量校验。只有采集等确实需要机器结构的阶段使用响应 Schema，迁移工作报告没有 JSON 表格要求。

`evidence_closure` 是已实现的领域 adapter：其 Schema 约束提议外形，随后 importer 按
`codex_job_result` 的 digest+ordinal 精确绑定并执行 Python typed validator；静态 materializer 再验证
真实文件、Git blob、外部输入和完整五件套 bundle。原始/结构化 Codex 输出仍只是 auxiliary。

## Gateway

- `CodexExecGateway`：首次创建 thread，返工和进程恢复使用原 thread；
  只重新注入变化的 Skill 文档，最新输入和反馈以文件路径交接；
- `CodexSdkGateway`：兼容入口，统一使用 CLI 传输，避免另一套恢复行为。

模型名称可按 Job 覆盖。thread ID 从同一工作组、角色、目录、权限、模型和配置绑定的会话记录恢复，
不能由 CLI 注入。每次调用及恢复均设置 224000 tokens 自动压缩阈值；sandbox 由阶段策略确定。
开发者流程从证据研究、环境、契约与测试设计到实现/构建/公开运行共用工作会话，
目标检查和最终证据检查共用另一独立会话。每次执行仍按阶段重新应用目录与权限，
不把上一阶段的写权限带到下一阶段。旧项目首次迁移复用当前阶段原有会话。
运行失败直接路由回实现者，重新经过快照、检查、包装和运行，不再额外启动修复模型。
详见 [新版对齐说明](WORKFLOW_ALIGNMENT.md)。

对 `INDEPENDENT` owner，这个 fresh-thread 行为只是必要条件。Job 还必须从未保留迁移对话的新
Codex 上下文/进程启动，并使用角色专属项目、凭据和材料挂载。共享迁移上下文或能读取候选/私测双方
材料的进程必须报告 `NON_INDEPENDENT`，不能靠再次调用 Gateway 恢复独立性。
