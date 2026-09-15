# Codex Job 契约

## Prompt 组成

阶段 Prompt 由 Prompt Pack 组合。默认 Pack 位于
`src/driver_port_factory/data/prompt-packs/default/`，包含：

- `manifest.json`：阶段到 Skill/reference 的映射；
- `job.md`：可自由调整的外层提示词，使用 `{{job_json}}` 和 `{{skill_documents}}` 插槽。

渲染结果包含四类信息：

1. 角色和不可跨越的信任边界；
2. 对应 Skill 的原 `SKILL.md` 和阶段 reference 原文；
3. 本次允许读取的 artifact/evidence 清单；
4. 任务目标与输出 JSON Schema。

Pack 和 Skill 内容不受语言或旧摘要限制。组合器在每次 Job 开始时重新读取当前文件，记录
Pack manifest、wrapper 和每份 Skill 文档的 SHA256，并把完整 Prompt 放入 CAS。因此开发者可频繁
调整提示词，同时仍能重放某次迁移实际使用的版本。只有正式 held-out 批次才通过独立实验配置
显式冻结 migrator、Prompt Pack、Skill 和预算。

项目可用 `dpf init --prompt-pack PATH` 选择默认 Pack；单次 Job 可用 `--prompt-pack PATH`
覆盖。控制器只校验 Pack 的可读结构和 Codex 输出契约，不校验自然语言措辞或与某个旧版本相等。

## Job 模型

```json
{
  "job_id": "uuid",
  "stage": "target_platform_study",
  "actor_role": "migration_operator",
  "objective": "完成目标平台 API 证据表",
  "input_artifacts": ["sha256:..."],
  "writable_roots": ["work/translated", "work/docs"],
  "output_schema": "schemas/codex-job-result.schema.json",
  "sandbox": "workspace-write",
  "model": null,
  "thread_id": null
}
```

控制器必须拒绝以下情况：角色不匹配、输入摘要不存在、输出 Schema 不通过、补丁越过可写路径、线程来自另一个信任域，或 Codex 试图直接修改阶段状态。

## Gateway

- `CodexExecGateway`：适合 CI/MVP，消费 JSONL 事件并用 `--output-schema` 固定最终输出；
- `CodexSdkGateway`：适合服务化运行，可启动/继续/恢复本地线程；
- `RecordingGateway`：测试中返回固定产物，不调用模型。

程序不固定模型名称；模型、预算和 sandbox 都属于实验配置，并写入 provenance。
