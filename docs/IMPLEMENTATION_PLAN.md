# 实施计划

## Milestone 0：规范固化（本轮）

- 架构、角色、状态和跨域 bundle 文档；
- 完整迁移阶段 DAG；
- Skill Prompt 版本化规则；
- 最小数据 Schema。

验收：文档能逐条映射三个上游 Skill 的硬性门禁。

## Milestone 1：可运行控制骨架（本轮）

- Python 包和 CLI；
- SQLite RunStore 与哈希链事件；
- SHA256 CAS；
- 角色化 WorkflowDefinition；
- clone 前请求解析、通用 Resolver/MetadataProvider、单次确认与范围冻结；
- source/target/QEMU revision 解析、bare repository、受控 worktree 和 materials manifest；
- 阶段依赖及 required-output Gate；
- Skill Prompt composer；
- Codex exec/SDK Gateway；
- 插件协议和候选物封存；
- 单元测试。

验收：能用任意版本化驱动 catalog 初始化项目、展示 DAG、快照 Prompt、登记证据并阻止非法状态转换；具体驱动只能存在于 fixture 或插件数据中。

## Milestone 2：环境、知识与结构化语义闭环

- 通用环境清单、artifact-mode 发现、恢复尝试和真实 `EXPERIMENT_READY` 运行；
- 接入现有 `kb.py`；
- 基于已冻结 compile database 的 AST/CFG/layout/effect 导出；
- 固定编译器与 compile database、验证七类 C 源码闭包并把新增原文回写知识库；
- 目标平台画像、API 证据表、analog trace 和检索修复；
- 目标 artifact identity 和 QEMU runner 插件契约。

验收：任意驱动能从输入三元组推进到结构化迁移 handoff；每个 `PASS` 都满足 Skill 对应门禁，不能由占位产物通过。

## Milestone 3：Codex 修复循环

- target study、contracts、Rust patch、failure diagnosis Job Schema；
- patch writable-scope 检查；
- 构建/QEMU 失败分类；
- 有预算的 affected-test/regression 循环。

验收：Codex 不能绕过 Gate；每轮诊断、补丁、命令和结果均可追溯。

## Milestone 4：设备类纵向样例与扩展

- 分别选择 Network、Block、Serial 的首个集成样例，不向核心加入样例特例；
- 然后扩展 I2C/SPI、USB；
- 可复用 source/target test adapter；
- 差分与 fault injection 后端。

## Milestone 5：独立盲测部署

- 策展和评测独立服务/系统用户/存储；
- prospective 与 post-hoc 时序验证；
- PMC/PEA commitment、mutation、stress、performance、hardware；
- 零反馈批处理和论文级聚合报告。

独立性由部署和凭据证明，不能仅靠新线程或子 Agent 声明。
