# 架构设计

## 1. 设计目标

DPF 是工作流控制器，不是一个拥有全局权限的长对话。它需要保证：

1. 每个结论都能回到固定版本原文、结构化事实或不可覆盖的运行证据；
2. Codex 只能读取阶段允许的输入并提交约定类型的输出；
3. 状态迁移、构建、QEMU、哈希、封存和结果分类由确定性代码控制；
4. 公开修复循环与正式私有评测严格分开；
5. 同一个设备类的测试场景尽可能跨平台复用，只替换平台适配层。

## 2. 组件

```text
CLI / API
   |
Workflow Controller ---- SQLite state + hash-chained event ledger
   |         |
   |         +---- Policy/Gate engine
   |
   +---- Artifact CAS + provenance
   +---- Deterministic command/build/QEMU runners
   +---- Codex Gateway (SDK or codex exec)
   +---- Skill Prompt sources
   +---- Source/Target/Device/Emulator plugins
   +---- Candidate sealer
   |
   +---- opaque transfer ----> isolated curator/evaluator deployment
```

### 控制面

`WorkflowDefinition` 生成角色专属 DAG，`RunStore` 保存阶段、产物连接和事件。阶段只有在所有依赖为 `PASS` 且当前 actor 拥有该阶段角色时才能开始。驱动范围存在歧义时，确认阶段进入可恢复的 `WAITING_FOR_USER`，问题和答案均写入事件链。Codex 输出不能直接写阶段状态。

### 产物与证据面

所有进入控制面的文件先写入 SHA256 CAS。数据库只保存摘要、类型、大小、来源和 CAS 相对路径。原始来源、派生索引、实现补丁、构建产物和运行日志使用同一种引用方式，但以 `kind` 区分。

### Codex 面

Codex Job 指定角色、目标、输入摘要、可写路径、Prompt 来源、输出 Schema 和预算。Gateway 可使用 Python Codex SDK，也可使用支持 JSONL 与 `--output-schema` 的 `codex exec`。线程只能在同一角色、同一任务域内恢复；盲测不能通过恢复迁移线程来伪造独立性。

### 插件面

插件分为四条正交轴：

- `SourcePlatformPlugin`：源码闭包、参考构建和源端执行；
- `TargetPlatformPlugin`：平台画像、驱动接线、产物生成与身份证明；
- `DeviceClassPlugin`：网卡、块设备、串口等可移植场景和 Oracle；
- `EmulatorBackend`：QEMU 参数、QMP/qtest、后端与观测收集。

不建立一个把所有驱动压成相同方法的万能接口。

## 3. 数据与信任域

开发/迁移、策展、评测使用独立项目目录和凭据。程序可以联网获取公开且与任务相关的资料；网络可用性不是独立性门禁。真正的边界是迁移域无法读取 PEA、私有 Harness、种子、checker 和私有结果。

跨域只传递显式 bundle：

- 策展域 → 迁移域：公开任务包、PMC、commitment；
- 迁移域 → 评测域：不可变候选包及摘要；
- 评测域 → 报告域：全部任务完成后的冻结结果。

代码可以位于同一 monorepo；运行数据、挂载目录、凭据和 Codex 上下文不能共享。

## 4. 状态语义

阶段执行状态使用 `PENDING/READY/RUNNING/WAITING_FOR_USER/PASS/FAIL/BLOCKED/INCONCLUSIVE/NOT_APPLICABLE`。证据结论另用 `VERIFIED/INFERRED/PLANNED/NOT_RUN/NOT_APPLICABLE/BLOCKED/FAIL/PASS`。`NON_INDEPENDENT` 和 `HARNESS_INVALID` 是评测分类，不能用普通失败覆盖。
