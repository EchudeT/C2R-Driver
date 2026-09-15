# Codex Job 契约

## Prompt 组成

阶段 Prompt 由四部分构成：

1. 角色和不可跨越的信任边界；
2. 对应 Skill 的原 `SKILL.md` 和阶段 reference 原文；
3. 本次允许读取的 artifact/evidence 清单；
4. 任务目标与输出 JSON Schema。

Skill 原文不是隐式的“最新文件”：组合器记录每份文档的 SHA256，完整 Prompt 也进入 CAS。由此可以重放某次迁移实际使用的 Prompt。
普通开发项目不锁定 Skill 文件，后续 Job 可以使用调整后的版本并产生新的摘要。只有正式 held-out 批次才通过独立实验配置显式冻结 migrator、Prompt、Skill 和预算。

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
