# Codex Job 契约

## Prompt 组成

阶段 Prompt 由 Prompt Pack 组合。默认 Pack 位于
`src/driver_port_factory/data/prompt-packs/default/`，包含：

- `manifest.json`：阶段到 Skill/reference 的映射；
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
execution_root, sandbox, output_schema?, model?
```

调用者不能通过 CLI 指定 sandbox、可写目录或 thread ID。`CodexExecutionPolicy` 从 typed stage 和
acquisition manifest 推导唯一 grant：

| 阶段 | sandbox | execution root |
|---|---|---|
| `rust_implementation`、`public_repair` | `workspace-write` | `work/target-working` |
| 其他 Codex/Hybrid/Independent stage | `read-only` | 项目根 |

可写 grant 必须位于当前项目内，且不能等于或包含项目根、`.dpf`、source baseline、target baseline
或 QEMU baseline，也不能位于这些目录之下。策略在启动 Gateway 前 fail closed。CLI 不提供提权参数。

## 输出与成功门禁

Gateway 成功返回后，控制器在 `.dpf/codex/` 写入 canonical result，并把它登记为 auxiliary
`codex_job_result`；JSONL 事件存在时另登记 auxiliary `codex_event_log`。这些原始模型证据不会改变
阶段状态，也不能直接声明 `PASS`。

CLI 不提供把模型响应转换为 required output 或直接 finalize 的通用入口。所有阶段都必须由明确的
领域 adapter 解析辅助响应或工作区变更，生成 typed artifact 完整 bundle，执行逐件 validator 和
跨产物 bundle validator，再通过唯一的 `Project.finalize_stage` 原子提交。当前许多后续迁移阶段只有
契约骨架，adapter 完整度以
`SKILL_TRACEABILITY.md` 的 `PARTIAL/PLANNED` 标记为准。

传入 `--schema` 时，exec Gateway 会把 Schema 交给 `codex exec --output-schema`，控制器并再次确认
响应为 JSON。控制器当前尚未独立执行完整 JSON Schema 校验，SDK Gateway 也没有等价的本地 Schema
enforcement；因此这一能力仍是 `PARTIAL`，不能作为已完成的领域 adapter 或独立 gate 报告。

## Gateway

- `CodexExecGateway`：一次性 ephemeral 执行，消费 JSONL 事件并提取最后的 agent message；
- `CodexSdkGateway`：延迟导入可选 SDK，按同一 execution root/sandbox 启动新 thread；

模型名称可按 Job 覆盖。thread ID 只是 Gateway 结果 provenance，不是可由 CLI 恢复或跨信任域注入的
输入。exec backend 总是带 `--ephemeral`，SDK backend 每个 Job 都新建 thread；没有 resume 路径。
sandbox 始终由控制器策略拥有。

对 `INDEPENDENT` owner，这个 fresh-thread 行为只是必要条件。Job 还必须从未保留迁移对话的新
Codex 上下文/进程启动，并使用角色专属项目、凭据和材料挂载。共享迁移上下文或能读取候选/私测双方
材料的进程必须报告 `NON_INDEPENDENT`，不能靠再次调用 Gateway 恢复独立性。
