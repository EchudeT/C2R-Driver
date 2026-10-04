# 运行前置与实现真实性审计

2026-09-26 更新：脚本级 QMP／runtime 绑定缺失改为 advisory，避免 helper 和外部控制器被文本检查误拒绝；高置信度 marker 仍拒绝。停滞保护现为三个不同提交、相同输入和观察，跨重启持久化。以下两次指纹与三类硬拦截描述是原始修复记录，当前策略见 [成本与质量优化](COST_QUALITY_OPTIMIZATION_2026-09-26.zh-CN.md)。

## 结论

`environment_recovery` 只证明 `EXPERIMENT_READY`：QEMU、设备模型或源基线能够按冻结路线执行。它明确不能证明迁移驱动已经进入目标制品。迁移驱动的运行前置必须在目标研究、迁移契约、实现自测、制品准备和公开 QEMU 阶段逐级闭合。

旧流程缺少一个由控制器执行的、QEMU 之前的最低真实性门。实现工作者可以写出文件、让脚本调用 QEMU，并在报告中描述预期结果；控制器没有在调用 QEMU 之前判断运行制品是否只是 marker、QEMU 是否被 `-S` 永久暂停、脚本是否绑定当前 runtime。第 14/15 阶段因此成为第一次暴露问题的地方。现象不能证明工作者故意偷懒；可以确定的是流程把不可执行的交付物留到了过晚的门，并且对相同失败没有熔断。

## 分阶段责任

1. `target_platform_study` 必须引用目标原始文件和行号，说明驱动注册、构建/打包、制品入口和第一条 probe 路径。缺少目标能力要进入 capability-gap，并指向目标框架阶段。
2. `migration_contracts` 必须包含 runtime insertion/driver presence、第一条 probe 和第一条适用数据操作的契约，每一项绑定目标证据、制品身份和 QEMU oracle。不能把未解决的前置留给实现阶段。
3. `target_framework_enablement` 只实现并验证已冻结合同所需的目标 API/框架能力；它不能用 loopback、stub 或其他 fallback 冒充硬件驱动能力。
4. `driver_implementation` 必须产生当前实现、runtime-artifact、presence checker 和 bounded smoke。控制器在任何 QEMU 调用前运行通用 preflight；只有 preflight 通过且实际执行证据满足原有 QEMU 边界，才允许封存实现快照。
5. `artifact_preparation` 重复 preflight、执行 presence checker，并把 preflight、runtime、checker 和 implementation digest 放进 attempt。源行为变化回实现阶段，制品/入口变化留在制品阶段。
6. `public_qemu_validation` 只能使用已经固定的制品。若重新检查发现实现或制品前置失效，控制器把问题路由到最小的已通过交付前置，并使下游证据失效。

## Preflight 的安全边界

Preflight 不读取驱动名称，也不匹配某个 API。它只拦截三个可机械说明的问题：

- 小型可打印文本同时包含多个 marker 术语，并且被脚本绑定到 QEMU 的 kernel、磁盘或 firmware 参数；这不是对“小文本”本身的否定，`-append` 等合法文本输入不会被拦截。
- QEMU 使用 `-S`，而脚本没有 continuation/QMP 命令；报告脚本中精确的 `-S` 行。
- 自测脚本完全没有 `DPF_RUNTIME_ARTIFACT`；报告实际 QEMU 行（找不到 QEMU 行时报告文件首行）。

每个 finding 保存 `code`、`path`、`line`、原文和理由，并在 worker continuation、失败 receipt 和阶段熔断消息中保留。Loopback、PCI、网络或其他具体 API 名称本身不构成 finding。QEMU 通过后仍必须满足 probe、数据操作、IRQ、边界和回归 oracle；preflight 不是功能正确性证明。

## 停滞与回退

控制器对每个 continuation 计算实质进度指纹：当前工作树文件状态、runtime/checker/smoke/public harness 字节、非 Codex 阶段产物和规范化失败原因。两次连续指纹相同就停止调用模型，阶段置为 `BLOCKED`，消息包含最后 receipt、精确 finding 和恢复动作。报告文字、attempt UUID 和 CAS 路径变化不算进度。

如果 finding 说明目标框架能力或接口缺失，worker 使用提交工具请求 `target_framework_enablement`；如果是当前 Rust、测试或 smoke，留在 `driver_implementation` 修复；如果是已改变的制品/入口，回 `artifact_preparation`。跨大阶段仍遵守 phase boundary，不能静默重开已封存设计阶段。

## e1000 旧运行的确定性结论

旧 workspace `/tmp/e2e-e1000-local-baseline-02` 的有效实现 attempt 使用了官方容器并观察到 QEMU，但 runtime-artifact 是文本 marker，smoke 在 `implementation-smoke.sh:12` 使用 `-S` 且没有 continuation，guest 没有启动，日志为空。新的 preflight 在执行 QEMU 之前会指出：

```text
runtime-artifact:1: small printable marker; not evidence of a bootable or packaged runtime artifact
implementation-smoke.sh:12: QEMU is started with -S and the script contains no continuation/QMP command
```

这两个 finding 不能被改写为 PASS。必须修复实现/制品和 smoke，或根据目标证据请求回到相应的前置阶段。
