# 工作流、提示词与 Skill 历史全面对照审计

日期：2026-09-20。性质：只读审计；本次只新增本文，不修改实现、提示词、测试或运行状态，不启动付费流程，不进行盲测。

## 1. 结论与证据边界

当前版本仍有会造成错误回退、无法自主纠错和错误放行的缺陷，不适合直接宣称已经稳定。主要问题不是“模型不够认真”或“审查太严格”，而是提示词允许的正常行为与控制器实际接受的行为不一致。

合并同根因后记录 **22 项：7 项 P1、14 项 P2、1 项 P3**。P1 是优先修复的流程正确性问题；P2 包括恢复、效率和证据表述问题，并非全部是已经发生的生产故障；P3 是残留接口和文档。本文不把条件审查、自然语言报告或静态账本本身当成漏洞。

审计快照：分支 `feat/workflow-cost-optimization`；HEAD `26fccdf28cc9963e49f6a6bcb1ee37161451e0bf`，**审计对象包含当前未提交改动**，不能用 HEAD 文件替代本报告的工作树证据。下文代码路径均相对 `src/driver_port_factory/`，行号对应此次工作树。

基准包括：

- 实际配置的上游 `../C-kernel-to-Rust-upstream/skill`，Git HEAD `6a75e6e98075690fa74e6cc2f2216a2a9510ba60`。主 Skill 的 SHA256：open-kernel `faa6aad22f15b582a798834f07508f5dff583eef1d889ceb67a02b51ae2425cc`；knowledge-guided `00f957befb1d7820f43ede3742748d098bb8d14056fda6ad38745eae88204603`。
- 已阅读的安装版 Skill 及相关引用，并复核实际上游差异；实际上游新增 `state.py` 路由要求，不能仅用安装版判断一致性。
- 用户指定的直接 Skill 历史：`/home/unix/.codex/sessions/2026/09/13/rollout-2026-09-13T16-56-26-01a099fb-6bca-7c81-8f75-7c77f42a30e6.jsonl`。
- 当前 prompt pack、渲染、上下文装配、工作目录权限、输出导入、验收、错误反馈和回退路径；旧运行成本与故障记录。
- 两个只读子代理分别检查前半、后半节点；主代理复查纳入项对应源码、实际 Skill，并对关键函数做最小复现。

证据强度区分为“复现”“代码确认”“历史观察”“待测收益”。没有重新执行驱动/QEMU，也没有逐一动态执行所有平台和异常分支。全面覆盖审计面不等于证明不存在其他漏洞。

## 2. 与直接 Skill 历史的比较

| 项目 | NE2000 直接 Skill | E1000 直接 Skill | 旧 cost-01 程序流程 |
| --- | --- | --- | --- |
| 时间窗口 | 09-13 09:21:35–10:44:47 UTC，约 83 分钟，含设备选择 | 实际续跑 09-14 15:12:49–17:23:11 UTC，约 130 分钟 | 多次暂停、返工；不能把总历时当模型计算时间 |
| 工具调用批次 | 357 | 活跃窗口 446 | CLI 事件口径不同，不直接比较数量 |
| 压缩事件 | 5 | 活跃窗口 4 | 持久 worker 已实现，仍须用新运行测效果 |
| 归一化费用 | 约 $32.18 | 约 $38.42 | 已知约 $85.76，另有 3 次未知费用调用 |
| 有效做法 | 同一 AI 实现、自检、定位 harness 故障 | 复用已有提取工具、修文档不重跑通过的 QEMU | 旧版曾有重复 compliance/review；当前已不再无条件执行 |

费用沿用 [既有成本审计](RUN_COST_AND_STABILITY_AUDIT.md) 的 token 差值，以未缓存输入/缓存输入/输出每百万 $4/$0.4/$20 归一化；不是中转账单，也不是本次查询的官方报价。E1000 成本窗口为 13:02:51–17:23:11，宽于上表活跃窗口。同一历史对话还含 Skill 开发等工作，不能对整条会话计费后归给迁移。版本与任务条件不同，尚不能据此承诺新版本一定低于 $30。

具体历史证据说明应保留什么、删除什么：

- NE2000 09:25:57 使用已有官方容器；09:26:17 浅克隆 QEMU；随后复用目标 baseline/cache。有效环境恢复是找到可跑路线，不是重建一切。
- NE2000 历史行 671–680：KB 建成 57 条记录后发现 ID 冲突，工作者修 manifest 并重建。**补充/修复知识库是实际用到的能力**，不能提示允许但程序禁止。
- NE2000 行 985–1024：创建过滤 AST 的导出脚本，修编译参数，真正运行提取和查询；E1000 行 3526、3608、3615、4062 复用导出器并查询事实。历史不支持“AI 从不读结构化事实，所以删掉 AST”。支持的是源代码为主、编译器事实按需补充。
- NE2000 09:49:56 先冻结合同和测试选择；09:55:44 自检发现 RX ring next-page 不变量问题。E1000 15:54:27 自检修正 TX gap 10/10/10 → 8/8/6。**自检有实际发现缺陷的价值**。
- NE2000 10:09:00 的接口枚举卡住发生在 ping 前、PCAP 为空；修改 harness，没有重写驱动。E1000 16:33:27 后按目标 ARP 语义调整应用重试，没有把首包丢失直接归为驱动缺陷。
- E1000 行 6187（17:08:12）、6383（17:19:06）：补最终证据与 handoff，复用冻结的通过结果，不重跑 QEMU。行 6508：修正哈希校验器的绝对/相对路径口径，不修改源码。

历史窗口中，NE/E1000 分别有 13/11 个工具批次涉及 AST/facts/export，32/28 个涉及 kb.py/index/materials。这是检索特征计数，单个批次可能包含多条命令，不能视为耗时或“每条资料都被读过”的证明。

旧 cost-01 的主要费用为实现 $45.25、public QEMU $10.41、旧 compliance $5.14、旧 review $2.25、实现前合计 $21.01。删除旧审查不是全部答案；错误回退、重复研究与大工具输出也必须处理。

## 3. 当前每个节点的输入、输出和验收

正常 developer 路径有 **20 个账本检查点、9 次必经 worker 阶段调用、条件触发的 reviewer**。不是 20 次 AI 工作。纠错/返工会增加调用次数；这里也不把“阶段调用”误称为新建独立对话。

共用输入为 job objective、当前上下文/反馈、执行根及工具信息、共用 prompt、新增 Skill 文档或已供给文档引用。下表列阶段特有输入。源码依据：`orchestration/migration.py`、`port.py:405–839`、`codex/policy.py:26–76`、`data/prompt-packs/default/manifest.json`。

| # / 节点 | 实际 AI 输入及任务 | 输出与程序接受方式 | 判断 / 关联问题 |
| --- | --- | --- | --- |
| 1 project_init | 无模型；项目配置 | 冻结身份与配置 | 必要静态状态，不算模型冗余 |
| 2 request | 无模型；原始请求 | 请求记录 | 没有额外 prompt 调用 |
| 3 driver_candidate_resolution | 当前入口走 intake 服务/元数据 | 候选驱动 | HYBRID 标签不等于实际调用模型 |
| 4 scope_confirmation | 当前入口唯一候选自动确认；歧义持久化问题 | 确认范围 | 保留一次确认语义，未发现确定新增缺陷 |
| 5 migration_envelope_freeze | 无模型；已确认范围 | 冻结入口、设备、总线和排除项 | 必要绑定，不应要求模型填哈希 |
| 6 revision_selection | envelope；选 source/target/QEMU 仓库及不可变版本 | 简洁 repositories JSON；控制器解析并固定版本 | 输出接口基本一致；受到 F18/F19 共用提示影响 |
| 7 repository_acquisition | 无模型；版本计划 | 仓库及源码身份 | 保留可复现获取；未证明这是主要费用源 |
| 8 evidence_closure | envelope、仓库清单；选择语义证据 facets/gaps | JSON；归一化后抓取、冻结和登记 | F01/F05；不是共用 prompt 所说的“通常不需要模型” |
| 9 environment_recovery | inventory、mode candidates；实际找到并运行路线 | Markdown + environment-smoke.sh；控制器再跑并观察执行 | F01/F08/F14；模式描述由报告承担，机械 PASS 证明范围较窄 |
| 10 knowledge_base | 无实际模型调用；受控语料 | build_infrastructure 静态构建 | 必要索引服务；F21 是旧 prompt/入口残留，不是实际多一轮 |
| 11 target_platform_study | target 路径/版本、KB 查询契约和生成 Skill | 一份 Markdown 绑定 profile/API/analog/change/report 等六种角色 | 不是六份报告；F06/F09；不应恢复繁琐表格 |
| 12 migration_handoff | 无模型；前置冻结证据 | 静态 handoff | 没有让另一个 AI 重写交接文档 |
| 13 source_closure | envelope、仓库/材料/gaps、KB、analyzer 和既往失败路径 | compile_commands.json + Markdown；预处理依赖闭包与 KB 扩展 | F13/F16；任务本身合理，编译参数应由工具验证 |
| 14 structured_c_analysis | 无模型；闭包/编译数据库 | AST/语义事实/索引；提取失败回 source closure | 不应整节点删除；F12/F16/F17，READY 不证明语义覆盖完整 |
| 15 migration_contracts | handoff、目标研究、KB、闭包和 C facts | 一份计划同时作为 contracts/test plan；只检查文档基本有效性 | 合并正确；F09/F10；自然语言语义由 worker 自检负责 |
| 16 driver_implementation | 计划、目标研究、KB、闭包、facts/index 路径 | 代码 + 一份自检 PASS 报告；Git inventory、hash、输入绑定 | F02/F03/F04/F10；隐藏 Git 限制不符合 Skill |
| 17 artifact_preparation | 计划、implementation/compliance、环境路线、目标 profile | runtime-artifact、check-presence.sh、报告；运行 checker 并检查快照 | F02；presence 内容仍信任 worker，见 F22 |
| 18 public_qemu_validation | 计划、覆盖报告、冻结镜像/identity、路线、KB、材料 | harness、fresh logs、报告 PASS 或最小前置 REWORK；控制器重新执行 | F01/F02/F07/F10/F14/F22 |
| 19 final_evidence_review | 无风险静态关闭；有风险才给 reviewer 代码/计划/运行结果与 risks | reviewer 单份 PASS/REWORK；只允许三个后期返工目标 | 收集全部实证 blocker 的 prompt 是正确方向；F10/F11/F15 |
| 20 completion_audit | 无模型；冻结产物和运行记录 | 完整性/归因汇总，状态 RECORDED | 非另一次 AI 全面审核；F22 限制需要明确 |

当前默认 pack 的 `output_schema` 均未启用。revision/evidence 的 JSON 是后处理解析；其他主要是文件交付。不能把旧文档的“自动 schema”当作当前保护。

## 4. P1：应先修复的正确性问题（7 项）

### F01：可修复的工作者输出/脚本错误绕过纠正回路

**代码确认 + 最小复现。** `port.py:304–338` 仅捕获 `CodexOutputError`，普通 `WorkflowError` 直接退出；恢复优先加载同一 pending JOB_RESULT。

受影响路径包括：`environment/execution.py:58–59` 缺脚本、`port.py:489–490` 环境执行失败、`acquisition/evidence_collection.py:107–112` 选定路径没有取回材料、`migration/public_qemu.py:199–200` 缺运行脚本，以及 `:271–300` 返回零但缺 fresh logs/QEMU 观察。反而部分运行失败和绑定错误已正确进入 correction，错误分类不一致。

主代理以 pending result 调用当前 `_codex_gate`，accept 抛 `WorkflowError('missing script')`，结果直接抛出、模型纠正调用次数 **0**。这是隔离函数复现，不是再次运行付费流程。

影响：resume 不等于修复，可能不断重放同一个坏脚本/答案。建议区分工作者可修复输出、基础设施不可用、控制器内部错误；前者回同一 worker 并携带具体失败证据，不要把全部异常一律重试。

### F02：正常新增报告或包装文件被误判为实现改变

**代码确认 + 最小复现。** `implementation.py:54–58,76–80` 把全部 Git changed/untracked 文件视为实现，仅排除 `.dpf-output/`；`job.md:64–75` 没有要求所有报告放该目录。implementation/packaging/runtime 的 cwd 均为 target worktree。

新增 `packaging-report.md` 完全符合提示，却会在 `artifact_preparation.py:43` 或 `public_qemu.py:205` 触发 `ImplementationChanged`，由 `port.py:730–735,789–791` 回退 implementation。同样影响更新旧报告和部分仅包装用途文件。

主代理 mock Git 集合从 `driver.rs` 增加 `packaging-report.md`，当前 validator 确实抛 `implementation changed paths differ from snapshot`。

建议：明确区分实现、包装、运行、报告的归属和失效关系，并在 prompt 提供真实路径约定。不能仅让模型猜唯一豁免目录，也不能把所有打包配置变化无条件豁免。

### F03：Skill 的本地 Git checkpoint 与验收条件冲突

**代码确认。** 实际上游 knowledge-guided `SKILL.md:18` 允许 Git 本地小阶段 checkpoint；`implementation.py:76–82` 却只看 `diff HEAD + untracked`，提交后代码变更集合为空。`:116,211–212` 还要求 HEAD 等于 acquisition 的 base commit；后续快照校验 `:52–53` 同样禁止移动。

影响：遵循 Skill 做本地提交也可能失败；留一个未跟踪报告不能解决基线不相等。建议以冻结 upstream 与当前工作结果的差异表达变更，分开不可变来源与本地阶段提交，不混用两种基线。

### F04：合法删除/重命名不能进入快照；symlink 前后判定不一致

**代码确认。** `implementation.py:87–98,226–237` 要求 changed path 仍是文件，删除旧文件无法表示，重命名旧路径也会失败，抛普通 `WorkflowError`。快照数据没有删除/文件模式表示。

另一个同属文件状态建模的问题：先 `.resolve()` 再 `is_symlink()` 可接受指向树内的 symlink；后续 `validate_worktree_snapshot:59–64` 检查未 resolve 路径却拒绝，造成先接受后回退。

建议采用一致的 Git 变更类型与路径策略，支持必要删除/重命名；若禁止某类路径，应在首次接受时明确拒绝并可反馈。

### F05：发现有效硬件手册也无法通过当前接口冻结

**代码确认。** evidence objective（manifest 第 38 行）禁止 hardware repository_paths，只允许外部 URL 作为 gap candidate。`proposal.py:285–304` 将 URL 转成 `ExternalReferenceLocator`；`external_material_retrieval.py:77–87` 即使下载成功，也因缺少 authority/binding 路径无条件抛 `UNAVAILABLE_PUBLIC_EVIDENCE`。

复杂的旧 `ExternalUrlLocator` 路径仍在，但不属于工作者被告知的简洁接口。影响是人为制造硬件证据 gap，不能把“成功找到并获取手册”变为 KB 原始资料。

建议提供简洁候选 URL/用途输入，让工具完成抓取、身份/来源验证和哈希；缺证据时才登记 gap，不恢复让模型填写哈希大表。

### F06：KB 要求“缺资料就补充并重建”，却没有可执行的受控路径

**代码确认。** 实际上游 KB 模板要求补 manifest/rebuild，`knowledge/skill_generation.py:105–116` 发布对应位置/命令。但 target study 只有 stage-work 的 workspace-write（`codex/policy.py:40–62`），不能修改外部 KB。即使绕过权限，`knowledge/corpus.py:34–44` 先检查冻结 manifest hash，修改后 rebuild 前即失败。

source closure 自动扩展 C 依赖不是补目标 API/规则文档的通用接口。直接 Skill 历史实际进行过 manifest 修复，说明不是纯理论需求。

建议保留冻结旧版本、增加受控补充/修复语料入口，自动更新查询版本；或明确回 acquisition 的可执行路径。不能要求 worker 修改控制器状态来完成 Skill。

### F07：QEMU 冻结镜像参数绑定存在确定的误接受

**纯函数复现。** `public_qemu.py:69–84` 接受 argv 任意元素等于镜像路径，没有判断该参数的用途。

输入观察行：`qemu-system-x86_64 -D /tmp/frozen.iso -kernel /tmp/other.elf`。这里 frozen.iso 是日志输出位置，但 `runtime_in_qemu_arguments(..., Path('/tmp/frozen.iso'))` 返回 **True**。

这仅证明该机械绑定检查存在 false positive，不是已经实际启动 QEMU或证明整条流程可被此单条件绕过。建议按支持的启动/磁盘选项和容器映射解析绑定；普通日志/名称参数不能证明镜像被启动。

## 5. P2：恢复、效率、提示词与证据边界（14 项）

### F08：环境 READY 比 Skill 要求的可执行实验路线弱

**代码确认。** `environment/execution.py:95–104` 主要判断存在 `qemu-system-*` 执行且 shell 退出零；`--version` 也可满足这部分条件。实际 environment Skill 区分版本发现与有效 smoke。

建议把工具可用与实验路线 smoke 成功分开，保留目标相关、低成本的最小 smoke 证据。不要求此阶段提前执行最终驱动测试。本次没有运行真实 QEMU 复现全节点。

### F09：REPORT_PATH 无效时静默降级为聊天文本

**代码确认。** `port.py:378–391` 只有唯一不同 `.md` 路径时才读文件；零路径、多个不同路径、错误扩展名可能直接冻结最终回复。target-study/contract 验收只要求非空 UTF-8（`target_study/validation.py:18–26`、`migration/validation.py:21–22`、`core/validation.py:165–171`）。

因此“已完成”或 `REPORT_PATH: /path/plan.txt` 可被作为研究/迁移计划文本接受。建议严格执行一个真实报告文件的最小协议，拒绝歧义并反馈；这不等于给 Markdown 增加完整语义 schema 或再开全面审查。

### F10：真实 blocker 和早期前置修复没有完整协议

**代码确认。** `review_policy.py:14–23` 要求 PASS，否则让 worker 修复或报告真实 blocker，但控制器没有相应 blocker 接受分支。`repair_routing.py` 只支持 implementation/packaging/runtime，无法表达补 source closure、目标研究、合同或证据的最小前置修复。

结果可能是重复 correction 后异常退出；当前没有证明旧运行的每次停机都由它引起。建议给受控停止/缺前提一个明确结果，并支持确有证据的最小依赖修复；不要让 worker 为通过而写假 PASS。

### F11：不同根因共用易重置的“审查返工预算”

**代码确认。** `port.py:122,144–159` 一个内存计数同时累计 structured analysis、packaging、runtime、review 回退，达到 3 次停止；进程恢复又归零。

影响：互不相干且有进展的三次修复可被停掉；同一失败跨重启却可继续消耗。建议按持久化失败指纹、实质进展和真实资源预算控制；不用“审查预算”解释非审查失败。

### F12：新 AST 裁剪遗漏按名字表达的 alias 依赖

**真实 Clang AST + 选择器复现。** 对 C `int helper(void); int entry(void) __attribute__((alias("helper"))); int helper(void) { return 42; }`，Clang 的 AliasAttr 含 `aliasee: "helper"`。

`source_analysis/ast_scope.py:35–65` 跟随 declaration ID/type，但不处理该名字依赖。以 entry 为 root、helper 为非 root 送入当前选择器，只保留 entry。实际风险场景是 helper 位于 root 目录外的共享头文件；不能把此例说成 NE2000 已丢函数。

投影验证在已裁剪输入上重建，只能证明自洽，不能发现遗漏。建议覆盖编译器中非 ID 的依赖形式，无法证明闭合时保守保留或标 gap，并提供按需扩展入口。另测的 CleanupAttr 含 cleanup_function ID，能被保留，**不列为漏洞**。

### F13：空编译 argv 触发未捕获 IndexError

**复现。** `_normalize_compile_command`（`source_analysis/closure.py:81–85`）对 `arguments=[]` 的 `all([])` 为真，随后访问 `[0]`。以现有 pyproject.toml 为存在的 file 调用，得到 `IndexError: list index out of range`；文件内容不参与此早期触发。

这本应是可反馈的 compile_commands 输入错误。建议检查非空数组/命令，保持可修复错误分类，不依赖异常字符串猜测所有输入错误。

### F14：工作者完成整套测试后，控制器强制再跑整套

**代码确认，节省幅度待测。** public objective 要求实际完成正反例、边界/恢复/重复检查并自检 PASS；`public_qemu.py:198,212` 随后无条件再次执行完整 harness（上限 3600 秒）。环境也要求先实际运行，再由控制器重跑。

第二次提供可重跑性和 collector 证据，但不是独立验证 oracle 的正确性。建议 worker 首次就经受控执行入口取得绑定结果，控制器验收该结果；保留 Skill 约定的冷启动/重复次数、变化后受影响重测及必要复核。不能为了省钱删除有行为价值的重复实验。

### F15：unsafe 审查触发看整文件，缺少变更风险粒度

**代码 + regex 复现。** `review_policy.py:32–36` 扫描 changed Rust 文件全文。只增加安全注册调用，也可能因原有 unsafe 或注释触发；纯注释 `// previous example: unsafe { access() }` 确实匹配。

prompt 已承认注释误报，因此不是完全隐瞒；但风险输入只有 path，没有新增/影响的 unsafe 边界，仍增加定位和重复审查成本。相同代码因包装回退重新进入此 gate，也没有机械复用已审查风险结果。

建议基于变更及其影响路径定位风险，并复用未变化的已闭合结论；不要机械认为“未修改 unsafe 行就绝无影响”，也不要恢复所有集成变更全审。

### F16：AST 裁剪后，KB 仍全文索引所有编译依赖头文件

**代码确认，当前耗时收益待测。** `source_analysis/closure.py:246–290` 收集全部编译器依赖，`:422–478` 全部追加语料并 rebuild，统一标为 `behaviorally-required-source-closure`。预处理依赖不等于每份头文件全部内容都与行为相关。

现有裁剪改善 AST/semantic 体积，没有同时解决 KB 的宽语料和检索噪声。建议保留完整依赖/哈希清单用于可复现，但默认检索索引以任务相关源码和可达声明为主，其他原件按需打开/加入；不能直接丢掉编译依赖证据。

### F17：查询工具仍有跨全项目重复完整性检查

**代码确认，费用影响待测。** `knowledge/cli.py:16–18` 的常规 status/search/show 使用默认 `open_project(..., read_only=True)`；`composition.py:112–132` 默认 `verify_artifacts=True`，打开项目会检查产物完整性。KB 索引自身还执行验证。生成 Skill 鼓励查询前 status，又增加重复打开。

c-facts 已改为范围验证，不能继续称它每次必校验全部 runtime image；但 `source_analysis/navigation.py:47–79` 仍验证 semantic 输入和索引，模型阶段也准备导航。

建议区分写入/阶段边界强校验与只读查询的受控证据集合复用；不要以不可靠的 mtime 缓存换稳定性。本次不把旧 2.4GB 体积当当前每次读取量，也未测当前累计 I/O 秒数。

### F18：上游 state.py 路由与控制器路由冲突

**实际 Skill 与 prompt 对照确认。** 上游 open-kernel `SKILL.md:12` 明确要求 `scripts/state.py` 做 status、保存决定和 advance；共用 `job.md:12–13,75` 禁止平行账本、由控制器推进。未明确声明哪一层替代上游这个操作接口。

可能造成双账本或 worker 停下来维护另一套路由。旧 cost-01 已检索的完成命令中没有 state.py 使用，**不把潜在冲突写成已发生费用**。建议系统适配说明控制器如何承担上游相同语义，避免同时发出相反操作命令。

### F19：共用 prompt 的阶段无关内容和术语歧义

**渲染测量 + 文本确认。** 共用模板包含运行脚本/镜像、审查、返工协议，连版本选择和早期证据选择也收到；`job.md:54` 的 “Evidence closure normally needs no model” 与实际 `evidence_closure` 必经 worker 冲突，所指显然更接近后期 final_evidence_review。

测量采用实际上游文档、空运行上下文、顺序 known_documents；单位为 UTF-8 字节，不是 token：

| worker 阶段 | objective 字节 | 渲染字节 | 本次新增 Skill 字节 |
| --- | ---: | ---: | ---: |
| revision_selection | 357 | 21,898 | 14,499 |
| evidence_closure | 1,564 | 12,041 | 2,934 |
| environment_recovery | 576 | 12,797 | 4,970 |
| target_platform_study | 348 | 27,228 | 19,602 |
| source_closure | 524 | 23,696 | 15,492 |
| migration_contracts | 1,046 | 20,230 | 10,861 |
| driver_implementation | 982 | 13,215 | 3,686 |
| artifact_preparation | 1,058 | 8,539 | 0 |
| public_qemu_validation | 1,136 | 8,619 | 0 |

共用包装 6,804 字节，正常九次约 61,236 字节；条件 reviewer 新上下文渲染 28,306 字节。数字不含真实 artifact context、工具输出、返工，也不能直接折算节省费用；缓存和上下文保留影响计费。

建议保留简短统一原则、当前阶段操作契约和所需 Skill 规则；把后期执行细则送到相关阶段。不要重复改写 Skill 全文，也不要把 Skill 真正需要的合同/测试思考误删为冗余表格。

### F20：模型 CLI 传输没有控制器级静默/总时限

**代码确认，未制造挂死实验。** `codex/transport.py:31–40` 阻塞读取 stdout 并 wait，没有超时监督。异常发生时可终止 owned process group，但进程活着且不继续输出时并不会自然触发该分支。

这不能证明旧第八/九步停滞均由此引起；CLI/provider 内部可能另有恢复。建议记录最后活动/进展、区分长工具调用与连接失活，提供有界恢复与准确状态，而不是对合理长构建盲目短超时。

### F22：完成审计的名字与语义保证范围不一致

**代码确认，属于信任边界/表述问题。** `public_qemu.py:97–105` 的机械 PASS 证明退出状态、QEMU 执行、参数绑定、fresh logs；oracle 内容仍由 worker 编写。`completion_audit.py:58–75` 由记录的 PASS/attribution 推导 target_driver_ran，由 identity hash 和非空 presence 字典推导 lineage；最终 contract_results/test_results 是空列表。

所以 completion_audit 是证据链记录/一致性检查，不是独立逐合同验证。不能称它已静态证明所有协议行为，也不能把“没有 Markdown 语义解析器”本身视为必须加第二模型的理由。

建议准确描述机械执行链、自检覆盖、条件审查三者的来源和边界；保留现有报告作为语义说明。不要求 worker 另填一套机器表格来制造看似独立的保证。

## 6. P3：旧入口与文档（1 项）

### F21：静态 KB 主路径仍留旧模型 probe prompt 和并存入口

**代码确认。** `port.py:492–493` 只调用 build_infrastructure；manifest 第 47–52 行却保留要求十类 probes 的模型 JSON objective，`KnowledgeBootstrapper.bootstrap(plan)` 和 `knowledge bootstrap --probe-plan` 仍保留另一套入口。

另有 `docs/CODEX_JOBS.md` 对自动 output schema、旧独立检查职责的描述落后于当前主路径。它们应随实现统一清理；不能计为当前实际多次付费调用。盲测是用户明确暂不运行的独立能力，本报告不据此要求删除整个盲测模块。

## 7. 不应误删的内容与子代理复核

以下判断已经在主代理复核时约束，避免“越审越多”的无效问题：

- 不把 20 个状态节点等价为 20 个 AI；intake、KB、handoff、AST、completion 的静态记录本身不是模型冗余。
- 不把一份目标研究绑定六种角色、同一计划绑定合同/测试矩阵、同一实现报告绑定 compliance，算成六份/两份重复写作。
- 不恢复已删除的无条件 compliance/reviewer，也不把旧运行审查费用认作当前必然费用。
- 不把所有 Markdown 缺乏强语义机器校验都当漏洞。F09 只要求真实报告文件协议，F22 明确信任边界。
- 不把所有 QEMU 重复都视为浪费。冷启动复现、边界/恢复测试、受影响重测仍有必要；F14 针对 worker 已完成后架构强制整套再跑。
- 不把 AST 一刀切删除。直接 Skill 历史确实使用编译器事实；当前裁剪还有 F12 的闭包缺口，需先保证正确，再减少无用索引。
- CleanupAttr 遗漏假设经真实 AST 检查被排除；alias 遗漏保留。普通 safe integration 是否触发 reviewer，按当前整文件 unsafe 规则判断，不沿用旧“所有目标修改都触发”的结论。
- 子代理的报告误回退、失败不反馈、Git checkpoint、外部资料、KB 修复、输出协议等已逐项回查源码。主代理独立复现 report drift、gate 不纠正、空 argv、QEMU 参数误绑定，并补查 alias、索引成本和 Skill router 冲突。

## 8. 建议修复顺序与后续验证口径

1. 先修 F01–F07：错误分类、文件/基线模型、证据入口和明确的 QEMU 绑定。否则压缩 prompt 省下的费用会被一次错误回退抵消。
2. 再修 F09–F13/F18/F20：严格但简洁的报告/阻塞协议、最小依赖修复、持久重试策略、AST 闭包、路由一致性和连接监督。
3. 接着优化 F14–F17/F19：受控执行复用、差异风险审查、按需知识索引、范围验证和按阶段供给提示词。收益用后续运行测量，不以字节缩减替代费用证据。
4. 同步修 F08/F21/F22 的验收边界和文档，使模型、程序、用户看到的是同一个标准。

后续验证应聚焦少量行为场景：普通报告不得回退实现；本地 checkpoint、必要删除/rename 按统一语义处理；缺脚本/日志在原 worker 获得完整反馈；真实 blocker 不耗尽无意义 correction；镜像日志参数不能冒充启动绑定；alias 依赖不静默丢失；同一受控通过结果可复用而不跳过要求的重复实验。

既往测试通过并不能替代上述路径验证。此次没有新增测试代码或运行完整测试套件；以上最小探针仅用于验证审计结论。下一次同等范围付费跑通前，**不承诺费用一定低于直接 Skill，也不宣称当前所有问题已经修复**。
