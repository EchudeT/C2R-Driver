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
- clone 前请求解析、轻量候选目录、单次确认与范围冻结；
- 阶段依赖及 required-output Gate；
- Skill Prompt composer；
- Codex exec/SDK Gateway；
- 插件协议和候选物封存；
- 单元测试。

验收：能初始化 NE2000 项目、展示 DAG、快照 Prompt、登记证据并阻止非法状态转换。

## Milestone 2：NE2000 纵向闭环

- Linux 与星绽OS平台插件；
- NE2000/PCI 身份解析和版本 pin；
- 接入现有 `kb.py`；
- Clang compile database、AST/CFG/layout/effect 导出；
- NetworkScenario 及 Linux/星绽OS adapter；
- 目标 artifact identity 和 QEMU runner。

验收：从输入三元组推进到公开 QEMU 结果和可重放候选封存。

## Milestone 3：Codex 修复循环

- target study、contracts、Rust patch、failure diagnosis Job Schema；
- patch writable-scope 检查；
- 构建/QEMU 失败分类；
- 有预算的 affected-test/regression 循环。

验收：Codex 不能绕过 Gate；每轮诊断、补丁、命令和结果均可追溯。

## Milestone 4：设备类扩展

- Block、Serial，然后 I2C/SPI、USB；
- 可复用 source/target test adapter；
- 差分与 fault injection 后端。

## Milestone 5：独立盲测部署

- 策展和评测独立服务/系统用户/存储；
- prospective 与 post-hoc 时序验证；
- PMC/PEA commitment、mutation、stress、performance、hardware；
- 零反馈批处理和论文级聚合报告。

独立性由部署和凭据证明，不能仅靠新线程或子 Agent 声明。
