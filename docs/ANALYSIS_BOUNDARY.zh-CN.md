# 分析会话、功能范围与前置职责

本次重构针对 pvpanic 06 的成本调查：分析首请求已有 162,679 input tokens，
两次低缓存命中请求占该阶段约 46% 费用。不能把截断后的必要补读、原始源码核对或全部报告输出
当作主要根因。证据与限制见 [成本调查](audits/target-study-cost-2026-10-03/findings.zh-CN.md)。

## 会话与交接

新项目固定 `scoped_worker_sessions=true`：

- 采集使用原 worker 会话；
- 环境使用 environment-worker；
- 目标研究和合并契约使用 analysis-worker；
- 连续实现、制品和公开执行保留原 worker 调度及现有 analysis→execution handoff。

首次分析不会继承采集/环境会话，同一分析的继续、返修和项目重启使用相同 analysis key。
没有删除历史、日志、失败记录或源码。实施中的单行为边界不变，不逐行为清空上下文。
现有 `context-policy` 仍管理 implementation handoff；persistent 不会跨越新项目的独立准备会话边界。
缺少 scoped_worker_sessions 字段的历史项目保持原会话布局，运行中的冻结 controller 不热更新。

`codex/stage_inputs.py` 注入显式输入，`target_study/inputs.py` 提供请求、设备身份、源入口、设备子集、
排除变体、所有仓库路径/版本和已有分析引用。功能范围独立传入 `functional_scope`；当前环境收据、
证据缺口、检索接口由已有任务输入提供。完整身份保存在短引用背后，不让模型抄写哈希。
新会话接收原件与控制器事实，不依赖另一个付费模型生成历史总结。

## 功能边界与冻结

设备身份只说明迁移哪个设备；功能范围说明迁移到什么程度。新 CLI 支持：

```sh
./scripts/run-experiment.sh --workspace RUN --driver-name DRIVER --behavior-scope scope.json
```

JSON 只含四项（示意任务，不是 pvpanic 实验的自动降级配置）：

```json
{
  "mode": "explicit-subset",
  "integration": "callback-harness",
  "required": ["在真实配置的设备上验证指定回调的成功、禁用和错误结果"],
  "excluded": ["安装真实内核调用链；本任务只验证所声明的回调边界"]
}
```

- `mode=source-driver`：覆盖选定驱动及其调用的共享驱动核心的可观察行为、控制接口、生命周期和错误路径，
  仅受明确 exclusions 限定。required 可补充特别要求；空 required **不表示无义务**。
- `mode=explicit-subset`：required 必须非空，覆盖所列行为及必要依赖。
- `integration=target-kernel`：需要真正的目标调用路径和制品接入。
- `integration=callback-harness`：允许明确的回调调用边界，但不能免除所配置的真实设备执行。

未指定时使用 source-driver + target-kernel，required/excluded 为空；不会为了预算默认改成 callback 子集。
范围在创建时复制进 project manifest，并记录在 migration envelope；后续所有工作者接收同一内容。
恢复时省略文件仍用原范围，传入不同内容会拒绝。修改外部 JSON 不会更改已冻结任务。
实验必须重开或使用明确允许的范围修订流程，不能修改项目状态绕过冻结。

该 JSON 固定操作方授权边界；具体源行为 IDs、适配及验收义务在同一份分析中展开并随现有合同冻结。
它不自动证明行为完整性，也无法机械判定两句自然语言是否语义矛盾。直接相同的 required/excluded
描述、空 subset、无效 mode/integration 会被拒绝。公共 benchmark 义务不能被报告或冲突范围削弱。
目标缺少某个机制是具体前提/范围问题，不自动获得丢弃源义务或实现整个无关子系统的权限。

## 前置分析的停止条件

在同一份报告中，每个范围内行为应明确：源依据、要求结果、目标承担者或接入路径、可区分的刺激/断言，
以及会改变行为或整体接入策略的必要前提。必要前提缺口须解决或明确阻塞。
局部方法名、辅助类型、文件组织、具体构建修改可以在当前实现行为中确认；不能提前声称 VERIFIED。

不再要求完整平台画像、预先列举每个 API、把相似驱动所有路径追踪一遍或以 VERIFIED 比例为门禁。
只为具体适配问题读相关原文。源义务、所有权/发布/清理、非阻塞提前返回和测试判据仍必须保留。
每项决定记一次，其余位置引用它，不在 profile、API 表、contracts、自查段分别复写。
这不是通过减少源功能、测试或必要语义约束来降费。

`analysis-job.md` 和 `analysis-task.md` 是工作者直接收到的单一职责约定。
本地 Skill 的 target-platform-study reference 已同步去掉冲突的全面调查要求；上游研究方法按需参考，
不重新启动其旧阶段流程。结构约定不新增模型、reviewer、章节格式或覆盖数量门禁。
分析报告仍经原有 provenance 校验及合同交付，最终验收不由模型自批。

## 08 的相关开发改动

`environment bootstrap RUN` 使用任务创建时指定的 Docker 镜像与加速器，合并平台准备、真实基线
构建/启动和短报告生成。模型接口只需要 `{"action":"bootstrap"}`，不接受环境选参。
已有当前有效基线时复用其收据；失败保留原记录，不自动改换镜像、固件或加速路线。
环境阶段不要求设备 probe；设备特定检查留在分析中的具体问题或实现行为内。
项目创建参数及宿主/容器边界见 [配置传递修复](CONFIGURED_PLATFORM_ROUTE.zh-CN.md)。

## 验证边界

回归使用合成仓库、替代模型和合成执行器，验证新会话拥有独立必要输入、同会话续做、重启保留范围、
范围变更拒绝、基线复用与失效证据拒绝。测试不发起付费模型实验，也不能证明真实 pvpanic 质量或降费。
测试记录保存于 `docs/audits/analysis-boundary-2026-10-03/`。

pvpanic 06 使用旧冻结版本：第13阶段首个行为结束后累计费用约 $11.66，触发 $10 的调用间预算上限而停止。
probe_control 已记录进度完成，panic_event/shutdown_event 和最终验收未完成。
这不是本重构的实验结果。单次长调用仍可能超预算，本次未实现实时服务端硬限额或自动提额重试。
