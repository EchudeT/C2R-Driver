# 07 / 08 的执行与资料复用优化

本轮针对 [pvpanic 开销诊断](audits/pvpanic-07-08-2026-10-02.zh-CN.md) 落实开发版优化。
不增加阶段、模型调用或 reviewer，不调整冻结验收。已运行的实验副本保持原协议。

## 已实现

### 环境执行模板

工作者在 `work/stage-work/environment_recovery/` 写设备探针 `probe.sh`，其中包含实际操作、
断言和正确的 QEMU 退出控制。然后调用：

```sh
dpf environment prepare-smoke RUN --image asterinas/dev:VERSION --probe probe.sh --timeout 120
```

控制器仅检查本地镜像并生成 `environment-smoke.sh`、`environment-container.json`，不拉取镜像、
不运行探针、不登记 PASS。入口统一处理当前工作目录挂载、工作目录、`-i`、禁用自动拉取、
无网络容器、超时、独立日志目录和命名容器清理。设备断言仍由工作者编写。
默认超时 120 秒，可选 1–600 秒；超时和非零退出不能变成成功。

提交仍走现有 report/submit 接口，由 `ExperimentExecutor` 实际运行和观察。
生成的配方绑定本地镜像 ID；验收还核对实际捕获的容器镜像 ID、探针和配方是否在执行中变化。
每次 attempt 保留配方、入口和探针副本，以及完整执行日志与归因记录。
探针应自包含；模板没有承诺自动追踪任意外部 helper 的传递依赖。

工具是常见容器 smoke 的可选执行包装，不处理所有设备/backend 配置。
有特殊挂载或其他运行要求时仍可明确提供原有自定义入口，沿用同一验收；不能自动改换路线。
环境设备模型可用不等于目标内核已构建/启动，也不等于 Rust 驱动已完成。

### 明确的失败诊断

控制器先保存失败 attempt，再返回具体的 `failure_reasons` 和日志位置，不再把失败 attempt
交给 PASS 校验器而只得到 `experiment_ready_run must have readiness=PASS`。
缺少工作目录挂载、无法归因 QEMU、执行超时/非零退出、采集器不可用等原因直接传递。
原始错误保留；当前阶段仍 RUNNING，不能通过文字答复改成成功。

### 材料定位与结构化清单

07 的工作包继续提供固定版本共享检索和本地原文路径；专用提示限定其职责为材料选择、
相关性说明和有依据的缺口。只为判断相关性读取必要片段，深入的目标 API/源语义分析留在
目标研究与实现中。已发现的关键事实写入原有 rationale，避免引入第二份报告。
没有文件数量上限，不裁掉理解当前驱动所需的依赖，也不把未检索到当成不存在。

```sh
dpf acquire locations RUN
```

这个只读命令返回 source/target/qemu 的固定版本、来源和基线路径，以及可写目标工作树，
不重新获取仓库。08 直接收到工具盘点、已有本地观察、候选路线和有界元数据导航；
完整清单和命令记录保留供按需核对，不必为了找路径逐页阅读。

提示词独立存放，便于审阅：

- `src/driver_port_factory/data/prompt-packs/default/evidence-selection.md`
- `src/driver_port_factory/data/prompt-packs/default/environment-task.md`

## 再次翻译的复用边界

环境配方可以显式跨开发任务复用：

```sh
dpf environment prepare-smoke NEW_RUN --recipe OLD_RUN/work/stage-work/environment_recovery/environment-container.json --probe probe.sh
```

新任务必须提供自己的探针。目标平台、target/QEMU commit、主机架构和本地镜像 ID 必须匹配；
新任务仍重新运行当前断言，旧 PASS 不导入。配方不跨盲评任务导入。
这是执行配方复用，不是自动复用上一个驱动的正确性结论。

共享知识库的 `capture`、`learn` 目前仍是显式入口。当前能复用已入库的原文、公开观察和
有来源的经验，不能声称“模型跑过一次，就自动记住整份研究”。新任务固定共享快照后，
运行中不会自动切换到随后发布的新知识。

尚未完成的闭环是：从已接受的研究产物稳定地发布可复用研究条目，并在新任务开始时根据
驱动范围、source/target/QEMU 版本和必要前提匹配，明确传入可复用结论、原文及变化。
当前没有自动复用 target study 整份报告或跳过其验证。应在这个闭环完成且测量第二次运行后，
才能宣称同驱动重跑减少了多少调研和费用。

## 验证

全量离线测试 322 项通过；随后补充的执行中探针变化、实际镜像不匹配及正常验收用例，
环境专题测试共 9 项通过。模拟 Docker/进程记录属于合成控制器测试，不是驱动验证。
本轮涉及的环境、导航模块与新测试通过 Ruff 检查/格式检查；全仓仍有既有 lint/格式问题。

另在独立临时目录用实际 `asterinas/dev:0.18.1-20260805` 执行一次 QEMU QMP 状态断言与退出：
命令退出 0、捕获 1 个 QEMU、官方容器归因通过，命令耗时 2929 毫秒。
记录见 [真实执行包装检查](audits/bootstrap-envelope-2026-10-03.json)。
这是执行机制检查，不涉及驱动实现或模型调用，不能据此宣称端到端翻译提速。
