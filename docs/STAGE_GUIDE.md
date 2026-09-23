# 阶段职责与审查回退索引

这份文件只描述 `driver-port-factory` 控制器的阶段边界和回退归属，不复制上游
Skill 的源语义、目标 API、测试或实现规则。技术要求仍以本次运行加载的原版
Skill 和各阶段已有输入为准。

阶段按四个大阶段组织。自动修复只能在当前尚未封存的大阶段内回退；进入后续
大阶段后，不能自动重新打开更早的大阶段。跨大阶段需要显式的 phase-reopen 决定。

静态阶段和 intake 的正常路径由控制器完成，不需要阶段工作 AI。若机械验收异常，
控制器可以临时调用 checker-decision AI 判断已有失败记录；该调用不拥有阶段产物、
不替代静态实现，也不能凭 ACCEPT 创建缺失输出。

| 阶段 | 大阶段 | 控制器职责 | 审查发现应回到 |
| --- | --- | --- | --- |
| 1 `project_init` | scope_and_baselines | 固定项目身份和控制配置 | 静态状态，不走模型回退 |
| 2 `request_intake` | scope_and_baselines | 保存原始请求和必需输入 | intake 记录 |
| 3 `driver_candidate_resolution` | scope_and_baselines | 解析唯一驱动候选 | 候选解析 |
| 4 `scope_confirmation` | scope_and_baselines | 固定设备、总线和包含范围 | 范围确认 |
| 5 `migration_envelope_freeze` | scope_and_baselines | 固定迁移边界和排除项 | envelope |
| 6 `repository_acquisition` | scope_and_baselines | 获取并固定干净的源码、目标和 QEMU 基线 | 仓库获取 |
| 7 `evidence_closure` | evidence_and_design | 固定证据材料、来源、哈希、覆盖和缺口 | 原始文件/文档选择、来源、证明材料缺失 |
| 8 `environment_recovery` | evidence_and_design | 固定 artifact mode 和可执行实验路径 | 环境、入口和实验路径 |
| 9 `knowledge_base` | evidence_and_design | 建立或验证只读知识库接口 | 知识库基础设施 |
| 10 `target_platform_study` | evidence_and_design | 固定目标 API、调用链、初始化顺序和目标修改证据 | 目标平台事实或目标修改记录 |
| 11 `migration_handoff` | evidence_and_design | 绑定前置证据并生成下游交接 | 受影响的交接记录 |
| 12 `migration_contracts` | evidence_and_design | 形成源码分析、迁移契约和测试来源记录 | 编译/预处理/布局/ABI/效果事实、契约或测试断言来源 |
| 13 `analysis_review` | evidence_and_design | 独立审查目标研究、分析、契约和测试计划 | 按问题类型回到第 7、10 或 12 阶段 |
| 14 `driver_implementation` | delivery | 生成 Rust 实现、适配测试和源快照 | 改动源码或实现快照 |
| 15 `artifact_preparation` | delivery | 构建、打包并证明运行产物身份 | 打包、镜像、入口或产物身份 |
| 16 `public_qemu_validation` | delivery | 执行公开 QEMU harness 并保存 receipt | 未变更产物的 harness、oracle 或运行证据 |
| 17 `public_repair` | delivery | 独立审查最终代码、产物、测试和运行证据 | 按交付阶段归属回退；不自动回到已封存的设计阶段 |

第 7、10、12 阶段的边界必须区分：

- 缺少或选错原始文件、外部文档、来源、哈希和受控材料，回第 7 阶段。
- 缺少目标 API 定义、类比调用点、初始化/选择顺序或目标修改必要性证据，回第 10 阶段。
- 缺少 C 编译配置、生成定义、预处理输出、结构体布局、ABI、volatile I/O 效果、
  翻译契约或测试刺激/断言来源，回第 12 阶段。

阶段状态 `PASS` 只表示该阶段的本地产物和自检被接受。第 12 阶段的 `PASS` 不等于
设计已经通过；在第 13 阶段独立审查通过并以提交工具提交 `pass` 前，不能进入实现。
