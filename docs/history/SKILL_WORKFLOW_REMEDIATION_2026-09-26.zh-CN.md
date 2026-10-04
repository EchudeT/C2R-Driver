# Skill 对照审查：全面修复台账

本文件承接 WORKFLOW_STRUCTURAL_REVIEW_2026-09-26.zh-CN.md。目标是通用驱动翻译的低总成本、高效率和可核验质量。历史费用不是受控对照，模型调用数不是美元节省，合成测试不是驱动认证。

## 问题与验收标准

| ID | 问题 | 修复方向与验收 |
|---|---|---|
| R01 | 交付开始后永久封住分析，缺少完整重开入口 | 开发模式允许带原因的最小前置修复，保留旧证据；盲评封存不放宽；重启后可继续，未变化的重复回退仍受限 |
| R02 | 报告与 artifact occurrence 使真实执行失效 | 执行缓存绑定可执行输入，不绑定解释文档；跨阶段重试复用观测，当前解释重新登记 |
| R03 | 整个公共脚本与全部 helper 一起失效 | 增加可选分项实验清单，每项声明脚本/依赖/义务；只重跑变化项，无清单仍支持普通脚本 |
| R04 | 本地执行与权威执行重复、反馈晚于任务结束 | 提供模型可直接调用的受控实验接口，返回完整回执，后续控制器消费同一证据 |
| R05 | 契约、实现、测试与结果关系仅在大报告中 | 轻量义务映射与实验结果联接；未覆盖项显式可见；不按 Markdown 标题或关键词设置新门禁 |
| R06 | 上下文策略仍绑定旧实现节点 | 两种 handoff 均绑定真正首个交付任务；诊断直接进入修复提示；独立最终审查有完整材料路径 |
| R07 | 分析/最终审查共用历史，可能锚定旧结论 | 最终审查独立会话且材料自足；同一审查的修复继续复用上下文 |
| R08 | 无显式平台资产复用 | 提供内容寻址、版本绑定的平台配方导入导出与验证；只提供事实，不复用旧 PASS；盲评禁用跨任务经验 |
| R09 | Asterinas 执行要求硬编码在通用逻辑 | 从已选运行路线推导容器边界，记录规则与身份；保留官方容器要求的证据，不按平台名推断所有运行 |
| R10 | validator 错误均可 worker 豁免 | 不可豁免结构/绑定错误与可裁决观测分离；控制器程序/配置错误不转成付费驱动修复 |
| R11 | 准备回执按接受时规则盖章 | 绑定任务实际 policy digest；规则变化不冒充已读；过期回执不能消除必要任务 |
| R12 | 部分材料获取失败不显著 | 分析与审查直接获得失败摘要和 retrieval ledger 指针，不因任一下载失败自动阻断 |
| R13 | 原始日志只存可写路径/hash | 执行时归档原始观测；复用前验证归档，不依赖后来覆盖的工作目录 |
| R14 | 模型/推理配置与成本复现不完整 | 记录无密钥配置、明确估算口径；离线控制器验证与真实迁移分开报告 |
| R15 | study 复用与组合报告语义/崩溃窗口不一致 | 只复用实际组合分析的有效回执；恢复不将旧目标研究冒充契约；重复接受幂等 |
| R16 | 审查只读主要依赖提示、结尾未核查源码漂移 | 审查前后绑定源码及运行证据，漂移不能发布旧快照 PASS；不把格式检查当语义审查 |

## 实施约束

- 继续使用优化分支，保留现有未提交改动。
- 不删除独立审查、运行身份、失败记录或必要行为验证。
- 不新增模型专职整理文档/填写框架表单的任务。
- 无变化的失败不无限重试；采集不足不自动等同驱动错误。
- 先完成离线故障/恢复回归，再考虑完整付费迁移。
- 每项完成情况和实际测试结果在下面更新；未验证项目不能标为成功。

## 验证与交付记录

### 实际实现

| ID | 本轮落地 | 验证/边界 |
|---|---|---|
| R01 | `core/phases.py`、`core/store.py` 允许开发模式带反例回退到分析前置节点 | 重启、保留源码/环境、DAG 失效测试；仍是阶段级依赖，不是自动语义依赖推断 |
| R02 | `migration/experiments.py` 独立执行身份及复用；公共报告仍单独绑定当前契约/解释 | 修改报告不重跑；源码、runtime、脚本、工具、路线或采集策略变化会失效 |
| R03 | 可选 `experiments.json`，逐项回执与依赖缓存；`--fresh` 强制新重复 | 只改 a 输入只重跑 a；依赖由作者声明，默认保守绑定全部 helper，未实现文件读取追踪 |
| R04 | `experiment run` 在 worker 任务内调用；`acknowledge` 将显式事后自检绑定报告与已见回执 | 融合路径可免去单独公共自检调用；没有确认则走原有自检流程；绝不以运行前报告代替事后自检 |
| R05 | `coverage.py` 联接可选义务映射与逐项结果 | 未执行项标记 NOT_RUN；OBSERVED 只表示运行观测；缺少/损坏映射是提示，不新增格式门禁 |
| R06 | handoff 策略移动到首个实际交付任务；失败诊断直接进入回退任务；材料提供 CAS 路径 | 按配置选择清空历史，默认仍为 persistent；已有 framework checkpoint 时实现节点承担边界 |
| R07 | analysis reviewer 与 final reviewer 使用不同会话键 | 独立首轮、同一最终审查的后续修改保持连续；最终审查可自行读取原材料 |
| R08 | `platform-asset export/import/status` 提供显式、版本绑定的平台事实与配方复用 | 验证版本/配置/hash；盲评禁用；没有实现跨项目二进制构建缓存，配置身份需使用者准确提供 |
| R09 | managed delivery 按已选实验路线的容器观测生成执行边界 | 容器身份失配拒绝；最初环境 bootstrap 与非项目级探针仍保留既有 Asterinas fallback，未宣称全部平台策略已移除 |
| R10 | `ObservationFinding` 与 `ControllerError` 分类；裁决接受前重新验证结构/绑定 | 结构和身份错误不能豁免；缺失/失败观测可基于证据裁决；控制器异常不交给付费 worker 修驱动 |
| R11 | prepared receipt 绑定实际 job policy；组合任务 digest 含后续职责规则 | 规则不匹配拒绝复用；不能在接收时将新规则盖在旧任务上 |
| R12 | `failure_summary.py` 给出有界失败摘要和完整 retrieval ledger 路径 | 已闭包但部分获取失败仍对分析/审查可见，不因任一下载失败自动阻断 |
| R13 | 每次执行保存原始日志副本，复用检查归档及已执行程序 hash | 工作日志覆盖不损坏旧观测；归档损坏重新执行；逐项证据在最终审查再次核验 |
| R14 | 记录 provider、配置的 reasoning effort 与既有模型/usage 信息 | 不记录密钥；未新增 CLI effort override；本轮没有真实费用实验 |
| R15 | `prepared_recovery.py` 从已接受报告的 job binding 恢复策略，修复接受 PASS 后崩溃窗口 | 分析恢复无需模型；组合研究标记必须真实；后续 consumer retry 阻止过期恢复 |
| R16 | 最终审查接收前核查源码快照、冻结 checkout、runtime CAS、逐项回执和归档 | 漂移不能批准旧证据；这是完整性约束，不是硬件功能正确性的证明 |

### 与 Skill 链路对照后的核心变化

将“模型工作边界”与“控制器证据节点”分离。框架适配、驱动实现、可运行产物及公共实验可以在一个持续任务内完成，节点仍逐一登记和验证。模型不必为登记同一份成果反复新开任务。实验在任务内可见，控制器随后复用相同输入的观测；失败时仍给出真实诊断并允许小范围修复。

纯 Skill 的优势是任务连续、反馈即时、较少为中间格式来回交涉。本轮借鉴这三点，但保留独立最终审查、原始观测和冻结输入。低成本历史运行与 factory 运行不是同配置受控实验，因此历史约 $36.94/$40 与约 $93.68 不能直接用于声明修复收益。

合成端到端路径对比：原交付路径 5 次 worker 调用，已准备产物路径 3 次，融合交付但下游自检路径 2 次，任务内完整实验且显式事后自检路径 1 次。这些数字只涵盖交付 worker，分析审查与最终独立审查仍保留，也不代表整个迁移只有一次调用。总 token、时延、费用及实际质量必须另做真实迁移对照。

### 新接口

模型提示中的 `tool_runtime.managed_experiment`、`experiment_self_review` 已带当前运行和 job 身份，应优先使用。手工形式如下（`RUN` 为运行目录，`JOB_ID` 为当前交付 job）：

```sh
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli experiment run RUN \
  --job-id JOB_ID --script .dpf-output/public-qemu.sh --timeout 3600
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli experiment run RUN \
  --job-id JOB_ID --suite
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli experiment acknowledge RUN \
  --job-id JOB_ID --report /absolute/path/to/work-report.md
```

先查看返回的观测，按契约完成报告，再 acknowledge。报告或当前执行回执变化后旧确认不再适用。`--fresh` 支持单脚本和整套实验，保留独立重复的回执。接口只在交付节点 RUNNING 时允许执行，不直接改变节点为 PASS。

可选 `.dpf-output/experiments.json`：

```json
[{"id":"probe","script":".dpf-output/harness/probe.sh",
  "contracts":["C1"],"dependencies":[".dpf-output/harness/input.json"],
  "timeout_seconds":300}]
```

各 case 应覆盖完整的可观测 QEMU/runtime/log 边界；当前不是任意单元测试命令的通用 runner。未声明 dependencies 时保守绑定全部 helper；声明错误可能造成遗漏，需要最终审查检查测试输入完整性。缓存只适用于身份中纳入的依赖；外部网络或未声明主机文件等可变输入需要纳入显式输入或使用 `--fresh`。失败观测保留但不作为成功缓存复用。

平台事实接口：

```sh
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli platform-asset export RUN \
  --facts /absolute/path/to/platform-facts.md --store /absolute/path/to/assets \
  --configuration architecture-config-toolchain-id
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli platform-asset import RUN \
  --asset /absolute/path/to/assets/HASH.json --configuration architecture-config-toolchain-id
```

导入提供可查阅事实，不能导入旧驱动 PASS，不能替代本次源码或 ABI 检查。

### 验证记录

- 本轮未启动付费模型或真实 E1000 全程迁移；此前成功运行不等于本轮修改已通过真实驱动验证。
- 定向交付/烟测/修复回归第一轮 43 项通过。
- 全量测试：`PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' -q --tb=short`，**186 passed in 197.67s**。覆盖任务内确认、报告/回执变更失效、CLI suite fresh、分项执行、跨阶段回退、重启恢复等。最后仅调整路线说明提示，随后单独回归规则身份与准备回执测试。
- 静态检查：关键语法/未定义名称检查通过；全部变更 Python 文件 F 检查通过；`git diff --check` 通过。仓库全量 F 检查另有 3 项既有未使用符号，位于 `completion_audit.py`、`sealing/candidate.py`、`tests/acquisition_support.py`，本轮未扩大修改范围。

### 尚不能宣称解决的研究问题

1. 缺少同源码、同模型、同配置的真实费用/质量对照；不能承诺降至 40 美元或具体百分比。
2. 新路线、真实硬件及不同驱动类型的效果需要真实样本验证；合成测试仅验证控制器语义。
3. 平台复用目前是显式事实/配方，不是自动构建产物复用；依赖目前是显式声明，不是自动追踪；分析回退目前是阶段级，不是契约级增量计算。
4. 独立审查降低历史锚定风险，但不能保证模型一定找出所有语义错误；仍需匹配设备行为的测试及后续盲评。


后续以减少事实搜集和语义返工为目标的增量实现，见 [预处理与早期反馈](TRANSLATION_ACCELERATION_2026-09-27.zh-CN.md)。该文独立记录新增能力、测试结果及尚未实现的研究方向。
