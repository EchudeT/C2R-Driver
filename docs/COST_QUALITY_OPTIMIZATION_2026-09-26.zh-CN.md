# 本轮成本与质量优化

分支：`feat/evidence-aware-cost-control`。依据 [通用驱动工作流研究](GENERAL_DRIVER_WORKFLOW_RESEARCH_2026-09-26.zh-CN.md)，优先实现可离线验证、低误拒绝、无额外模型调用的改进。

## 已实现

| 改进 | 行为 | 成本／质量边界 |
|---|---|---|
| 历史调用计时 | 缺少 completed_at 的旧调用使用已记录 elapsed；只有同一活跃 controller 的 RUNNING 调用继续计时 | 未知 usage 不补零，不修改历史费用 |
| 调用终态 | COMPLETED / FAILED / INTERRUPTED 在 finally 中持久化 | 中断也记录完成时间；CLI 直接调用同样持有 controller lock |
| 低误拒绝 preflight | 暂停／缺变量仅 advisory；可识别的非制品 marker 仍为 error | 不用设备名称、Loopback、TODO 或报告关键词拦截 |
| 轻量制品检查 | 大制品先查 size；不为识别短文本 marker 把整个镜像读入内存 | 脚本型制品不因 marker 注释被拒绝；PASS 不代表可启动 |
| 持久续调观察 | 三个不同提交且输入和实际观察相同时才暂停；计数写入 ledger | 重启不清零；同一提交重放不计数；显式 stage reopen 带原因可开启新修复周期 |
| 更准确的进度输入 | helper 摘要、presence/harness 退出、timeout、容器、制品绑定、日志观察参与判断 | 字节指纹仍是保守近似；不声称已经实现语义进展判定 |
| 两套保护分工 | public operation 由已有 operation guard 处理，观察后自查不计入失败续调 | 避免新的保护抢先拦截正常自查 |
| smoke 复用版本绑定 | collector、preflight 和 smoke 规则代码参与复用 identity | 改报告仍复用；更换校验规则重新采集；首次旧 receipt 可能需要重验 |
| status 证据层级 | execution 与 functional_assessment 分开，JSON 提供报告路径／digest | 不新增自动语义验收门；review 不等于独立盲测 |
| 显式会话重置 | 免费生成 CAS handoff、归档旧会话，下次新建 thread 并重载规则 | 默认不重置；新会话后续费用仍需实验，不承诺节省比例 |
| 测试代理隔离 | localhost HTTP fixture 绕过代理，保留原 NO_PROXY 条目 | 不修改生产网络策略 |

Prompt 小幅补充：优先完成真实构建／接入路线与首个设备操作；区分事实、假设、过期归因和未决义务；按 symbol/section 查询，完整日志落盘；禁止通过缩小 oracle 来改变冻结功能范围。使用现有报告，不新增一套模型必填大表。

## 使用新会话

```sh
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli codex reset-session \
  /path/to/run driver_implementation --reason '旧归因已被新运行证据否定'
```

如果原运行使用显式 `--model`，重置时提供相同参数。session identity 仍绑定项目、角色、模式、provider、model、backend；developer 下 worker 跨阶段共享，因此此操作旋转的是该身份的整个 worker 对话，不是只删除某一个阶段。reviewer 使用另一会话。

只允许 developer-evidence 模式，避免将全局交接包带入角色隔离的盲测流程。控制器运行中会因已有锁而拒绝重置。操作保留原代码、阶段状态、预算／停滞记录和历史会话；BLOCKED 阶段仍需要既有 `stage reopen` 流程，不会因重置自动通过。

handoff 内容由控制器生成：scope、阶段状态、当前产物引用、当前报告和最后 continuation。报告仍是待核对的主张，不自动升级为事实。新会话首次调用携带 handoff 路径并重载必要 Skill；成功建立后继续正常 resume。若会话在首次调用中断，不会丢失 handoff。CAS 校验不通过时明确报错。

新会话不额外自动调用摘要模型，不复制长日志，不清除外部历史。未决义务仍需从冻结契约及报告读取；这不是完整结构化义务图，也不是自动最优上下文调度器。

## 检验策略

本轮以通用正反例为主：helper 控制 QMP、脚本型制品、同提交重放、helper 真修复、新 runtime 观察、report-only 修改、明确解决外部问题后的 reopen、调用中断、会话重置后规则与 usage 基线、执行 PASS 与独立 review 的区别。

主 e1000 workspace 只读核对：`stage_work` 从错误累计的约 7 小时修正为 sidecar 记录的 1,694.481 秒；已知费用仍为 $9.3260424，不把未知调用当免费。没有恢复付费迁移，也没有重跑旧 QEMU。

最终全量回归：`PYTHONPATH=src uv run --no-project --with pytest --with jsonschema --with referencing python -m pytest -o addopts='' -q`，73 passed（79.19 秒），不再需要调用方设置 NO_PROXY。新增模块与新增测试 Ruff 检查通过，`git diff --check` 通过。仓库既有大型函数复杂度／格式 lint 尚未全清，不能据此宣称全仓 Ruff 通过。测试验证控制器行为，不构成真实驱动迁移成功率或 token 节省比例的实测。

## 后续科研功能

结构化义务图、独立 oracle 适配器、设备类别 benchmark、自动按停滞重置和模型路由仍需单独实验。这一轮没有用文本扫描替代它们，也没有宣称 status 的功能评估来源就是完整覆盖率。先用本分支收集可比较的调用／失败数据，再决定是否增加额外门禁或自动策略。
