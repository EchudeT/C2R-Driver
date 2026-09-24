# 阶段职责与 Prompt 复核

复核范围是当前 `DEVELOPER_EVIDENCE` 的 18 个迁移阶段。核对了 Prompt Pack、
`orchestration/protocol.py`、`PortRunner` 的实际调用与产物验收，并沿着每个阶段
注入的原版 Skill 文档阅读了对应的工作流章节。这里记录控制器职责和提示边界，
不复制 Skill 的硬件、API、语义或测试规则。

`project_init` 到 `migration_envelope_freeze`、`knowledge_base`、`migration_handoff`
是控制器静态流程；`driver_candidate_resolution` 和 `scope_confirmation` 虽然在
阶段类型上是混合阶段，但当前入口由 `IntakeService` 根据目录和用户回答确定，
正常路径不会调用 AI。任何阶段在机械验收异常时都可能触发一次 checker-decision
AI；那只是判断已有失败记录或恢复当前操作，不能代替该阶段的静态实现，也不能创建
缺失产物。真正有 AI 工作任务的是采集、研究、契约、交付和独立审查阶段。

| # | 阶段 | AI 参与 | Prompt / 交付 | 实际控制器行为 | 职责判断 |
|---:|---|---|---|---|---|
| 1 | `project_init` | 正常无；异常可 checker-decision | 无模型任务 | 固定项目身份、角色和配置 | 清楚；静态状态 |
| 2 | `request_intake` | 正常无；异常可 checker-decision | 无模型任务 | 保存原始请求和必需输入 | 清楚；静态状态 |
| 3 | `driver_candidate_resolution` | 正常无；异常可 checker-decision | 无模型任务 | 轻量元数据解析候选 | 清楚；不要误认为需要模型读源码 |
| 4 | `scope_confirmation` | 正常无；异常可 checker-decision | 无模型任务 | 自动确认唯一候选或等待一次用户回答 | 清楚；不要让模型代替用户确认范围 |
| 5 | `migration_envelope_freeze` | 正常无；异常可 checker-decision | 无模型任务 | 固定驱动、设备、总线、包含和排除范围 | 清楚；封存边界由控制器拥有 |
| 6 | `repository_acquisition` | 是，JSON 选择 | 只返回 source/target/qemu 的仓库选择；控制器获取、解析 revision、记录锁和 provenance | 复用 supplied baselines，完成实际获取和校验 | 原版 acquisition Skill 负责版本与材料规则；Prompt 只补选择边界，清楚 |
| 7 | `evidence_closure` | 是，JSON 选择 | 选择 facet、理由、原始路径/外部文档和显式 gap；控制器获取内容、哈希和 provenance | 导入选择后生成材料清单、覆盖和 gap ledger | 原 Prompt 容易把“证据缺口”扩大成语义缺口；现已明确只负责材料选择和来源，不负责编译器、C 语义、契约或执行 |
| 8 | `environment_recovery` | 是，报告和脚本 | 写环境入口和报告，提交给控制器执行 smoke | 固定 artifact mode、experiment route，运行入口并保存 receipt | 原版环境 Skill 的 recovery/fallback 仍由 Skill 提供；Prompt 已补充不负责目标 API、契约和实现 |
| 9 | `knowledge_base` | 正常无；异常可 checker-decision | 无模型任务 | 构建只读 KB、query contract 和项目 Skill | 清楚；语义 probe owner 是第 10 阶段，不应另起模型任务 |
| 10 | `target_platform_study` | 是，报告 | target profile、API 定义与调用点、analogous path、初始化/所有权/上下文、包装路径和 target-change 证据 | 只接受工作报告，保存为 target study artifact | 原 Prompt 过于像“完成一份报告”；现已明确不实现 Rust、不闭合 C 契约、不准备 runtime、不跑公共 QEMU |
| 11 | `migration_handoff` | 正常无；异常可 checker-decision | 无模型任务 | 绑定前置 artifact 并生成 handoff | 清楚；不让 AI 重复整理控制器已有身份记录 |
| 12 | `migration_contracts` | 是，报告 | 同一报告完成 source closure、按需 compiler/preprocessor/layout/effect 取证、contracts 和 test provenance | 把同一报告绑定到 contracts 与 test matrix 两个视图 | 清楚；Prompt 已明确不实现、不改 target、不准备 artifact、不跑 QEMU；独立审查仍负责实质验收 |
| 13 | `analysis_review` | 是，独立审查 | 只读核对研究、分析、契约和测试来源，一次返回全部问题，精确引用行号/原文 | 只有 reviewer PASS 才进入实现；REWORK 路由到最早受影响的同阶段前置 | 与 review Skill 对齐；实现和运行结果不是本阶段缺口 |
| 14 | `target_framework_enablement` | 是，目标文件/检查/报告 | 消费目标研究和冻结合同，实现最小目标 API/框架能力，写必要性、替代方案、安全影响和回滚；不改驱动或引入 fallback | 控制器校验目标快照、报告、change inventory 和文件漂移 | 新增独立能力门；后续驱动只能消费该快照，重叠路径直接拒绝 |
| 15 | `driver_implementation` | 是，代码/测试/报告 | 只消费第 14 步目标框架快照，完成 Rust、公共测试、合规自检和快照 | 控制器检查快照、测试和 implementation bundle | 目标框架改动与驱动改动职责分离；正常路径不负责 artifact/QEMU |
| 16 | `artifact_preparation` | 是，脚本/产物/报告 | 使用现有实现准备 runtime、variants、presence checker 和身份；行为变化回实现阶段 | 控制器运行 checker、记录 identity，并在必要的包装改动后刷新实现快照 | 已明确这是包装/注入/身份节点，不是公共 QEMU |
| 17 | `public_qemu_validation` | 是，脚本/同一报告复核 | 消费第 16 阶段固定的 artifact；写 public runner，申请执行，读取 receipt，完成 phase 8/9 的运行与归因自检 | 控制器执行一次绑定脚本并把观察结果交回同一 worker | 只负责未变制品的 harness、oracle 和运行证据；最终独立审计归第 18 阶段 |
| 18 | `final_evidence_review` | 是，独立审查 | 只读核对当前代码、target framework、target change、冻结 contract/test oracle、artifact 和运行日志；不重审第二阶段设计材料，一次返回全部发现 | reviewer PASS 才完成交付；REWORK 只回到 14/15/16/17 | 与 final evidence audit 和专用 final-review prompt 对齐；不让 reviewer 代写修复 |

## Skill 路由结论

- 第 6–8 阶段分别加载 `open-kernel-driver-port` 的 acquisition、handoff 和
  environment recovery 原文；控制器协议只定义选择格式、报告入口和执行接口。
- 第 10 阶段加载 `target-platform-study.md`、`knowledge-contract.md` 和 profile
  模板，覆盖目标 API、类比路径、上下文/所有权和目标变更必要性。可选的
  Linux/Asterinas 导航 Skill 只是原内核导读，不能替代本次冻结原文。
- 第 12 阶段加载 workflow、translation、knowledge-contract、test-porting 和
  qemu-evidence；按需编译器取证是本工作流对原 Skill 的已批准执行适配，不是删掉
  语义要求。
- 第 14–18 阶段分别加载 target-framework-enablement、translation/target-changes/test-porting、qemu-evidence
  和独立 review 规则。`review.md` 负责精确行号、定位工具和集中反馈，不重复写入
  Skill 的技术标准。

## 仍需保留的边界

阶段本地 `PASS` 只证明本地产物和协议被接受。第 12 阶段自检不是独立实质审查，
第 13 阶段的 reviewer PASS 才允许进入目标框架和驱动实现；第 18 阶段再审查最终代码、目标框架和运行证据。
这三个层次不能由一个更长的阶段 Prompt 互相替代。

`target_platform_study` 当前的确定性 validator 仍主要检查报告是可读 UTF-8，
而 API 表、类比路径和 target-change 证据由该阶段 Skill 自检及第 13 阶段独立审查
核对。这样没有增加新的模型节点，符合成本约束；若以后要把这些内容做成静态门禁，
应新增结构化产物协议，而不是把更多技术清单堆进共享 Prompt。

本次复核没有启动新的付费实验，也没有读取或修改 `../e2e-ne2000-audit-10` 的现场。
