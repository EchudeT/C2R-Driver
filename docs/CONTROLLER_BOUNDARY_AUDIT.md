# 控制器边界复查（2026-09-19）

范围：模型结果到静态工具、环境/包装/公开运行脚本、退出/超时、恢复、状态显示、
证据分类反馈、导航索引及统计。检查期间未恢复付费控制器，也未编辑工作者脚本。

## 已确认并修复

- 环境、产物 presence checker、公开 QEMU 三处均强制 `/bin/sh`，忽略工作者脚本的
  Bash shebang。统一使用 `script_command`：尊重声明的解释器，未声明则 Bash；
  不要求可执行位，不经 shell 拼接路径。三个边界均加入 Bash 特性回归测试。
- `CommandRunner` 超时只杀父进程。现在独立进程组，超时或中断清理拥有的组内子进程，
  保留 stdout/stderr 和超时结果。测试检查子进程不继续运行。
- 只有阶段 `RUNNING`，没有控制器存活信息。`port run` 现在持有项目级排他锁；
  `status` 单独显示 ACTIVE/STOPPED/UNTRACKED，JSON 也返回该字段。重复控制器拒绝，
  异常释放锁，SIGTERM 转为可清理中断。旧进程没有锁时显示 UNTRACKED，不猜测存活。
- 第八步默认 prompt manifest 与程序内目标存在不一致，已同步权威来源规则。
  逐条失败改为一次反馈所有无效 facet；不放宽证据授权、不把源码冒充硬件手册。
- 流程恢复仍先复验持久化模型结果，工具修复不要求重新生成成果，不跳过最终审查。

## 真实验证

原第九步 `environment-smoke.sh` 未修改，通过修复后的脚本命令、CommandRunner、
strace 和 ContainerTrace 独立复验：exit=0、timed_out=false、QEMU observations=1。
记录：`/tmp/dpf-real-harness-audit-k36h5vxo/`。日志与正式账本分开，未将此诊断写作阶段 PASS。
脚本正常产生新的环境运行日志和缓存；未启动模型、未修改驱动。

## 不能掩盖的边界

- 全量测试运行记录在 `/tmp/dpf-audit-tests.log`。8 个文件无法收集，原因是依赖已删接口；
  初次还有 12 项失败，其中本轮增加控制器锁导致的 2 个测试夹具缺 control 目录已修复。
  其余涉及旧 target-study/contract CLI、command_records，以及旧环境路由的验收假设。
  它们仍须迁移/判定，不能将全量测试说成通过。没有增加运行时兼容层或删除这些测试。
- `ExperimentExecutor.run` 的独立计划入口与主流程的 `run_codex_harness` 验证强度不同：
  前者按约定命令退出码判断实验，后者要求真实 QEMU 观测。全量测试暴露了这一差异；
  不能用前者的 PASS 替代本轮环境或驱动运行证据。
- 进程组清理不等于清理 Docker daemon 管理的容器；脱离组的进程同样不受此保证。
  本轮正常运行使用新容器与 `--rm`；异常终止后仍应核对该次容器，不能批量停止其他容器。
- ACTIVE 只证明控制器持锁，不证明模型正在产出。判断停滞仍需最新事件、子进程和工具日志。
- 阶段时间仍是墙钟时间，包含停止期间尚未结束的 RUNNING 区间；没有伪装成纯计算时间。
- 本次是控制器边界审计和环境实测，不是新一轮 22 阶段验收。付费流程保持停止。
