# 上下文日志与完整实验留存

每次通过 `run_codex_stage` 发起调用时默认留存上下文，不需要模型写日志或增加 API 调用。新增日志位于 `RUN/.dpf/codex/context-logs/`，原来的提示产物、事件流、提交回执及用量 sidecar 继续保留。

## 保存什么

每个 job 对应一个 JSON 清单，保存精确提示对象、阶段、请求续接的线程和实际线程、上下文策略与 epoch、交接引用、提示策略摘要、压缩阈值、用量基线，以及原生 rollout 的快照。

原生日志按准确线程 ID 从本机 Codex 索引或 sessions/archived_sessions 路径定位。读到的 session_meta 必须匹配线程后才保存内容。不复制全局数据库、配置、环境变量或其他会话。格式依据当前安装的 CLI 实际记录；官方非交互接口说明见 https://developers.openai.com/codex/noninteractive 。原生落盘格式属于兼容性适配，不假定其永远不变。

快照保存当时的精确字节前缀、SHA256、大小及分块列表。按 1 MiB 内容块去重，不在每轮复制完整历史；每次的新尾块和新内容会占用额外磁盘。相同快照不重复加入清单，源文件随后消失也保留已有快照。

采集发生在调用前、线程开始、turn.completed、调用结束（包括失败和普通中断），以及有流式事件到达时最多每 60 秒一次。不是独立后台采样器；长时间无事件或强制 SIGKILL 时，只保证此前完成的检查点。正在追加的 JSONL 最后一行可以不完整，报告明确显示尾部字节数。

流式事件日志还记录非 JSON stdout；正常结束时保留 stderr 最后 8,000 字节并注明是否截断。突然中断时 stderr 尾部可能未写入事件日志，不能声称捕获全部进程输出。

日志错误只记录为 capture_error，不阻断迁移，也不触发额外模型调用。`.metrics.json` 和 `context-report` 中的 context_log 指向对应清单；如果清单自身无法落盘，metrics 仍记录错误。

## 如何查看与还原

```
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli codex context-logs RUN
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli codex context-log-export RUN JOB_ID /tmp/retained-rollout.jsonl
```

导出默认选该调用的最后一份已捕获快照；`--snapshot 0` 可选择第一份。导出验证块摘要和整个文件摘要，拒绝覆盖现有文件，验证失败删除未完成的输出。原生快照是累积历史，不能把每个快照的 token 或压缩次数相加当作本次费用/次数。

审计命令核对已有快照并统计顶层记录类型、明确的 `compacted` 记录、无法解析的完整记录及尾部字节。没有日志时为未知；顶层 compacted 为零，只代表该格式标记没有出现，不证明服务端或其他格式从未压缩。原生日志不是服务端逐请求抓包，也不能还原未公开的模型内部状态。

日志包含实际提示、代码和工具输出，可能夹带工具打印的敏感内容，因此默认只保存在本地，新建日志目录权限 0700、对象与清单 0600。不会上传日志，也不会将原始会话加入本仓库。为完整实验保留整个 RUN 目录，尤其 `.dpf/codex/`、`.dpf/cas/`、数据库和工作区；不要只保存最终 Markdown 报告。

## 下一次完整 e1000 实验

使用新目录保留旧失败运行，固定模型并显式选择策略。建议工作区放在持久目录，避免 `/tmp` 清理：

```bash
scripts/run-experiment.sh \
  --workspace /home/unix/file/C2R-Driver/c2rust-migration-test-01/e2e-e1000-context-logs-01 \
  --driver-name e1000 --source-platform linux --target-platform asterinas \
  --model gpt-5.6-sol --context-policy analysis-handoff \
  --local-source-repository /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux \
  --local-target-repository /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas \
  --local-qemu-repository /home/unix/file/C2R-Driver/.driver-port/ne2000/upstream/qemu \
  --analysis-review --final-evidence-review
```

这次以评估当前整体工作流为目的。审查开关与历史运行不同，仓库路径相同也不保证最终选择的 commit 相同；必须以新运行的冻结清单为准，不能称作只改变上下文策略的因果对照。

运行后结合日志核查：分析到执行是否只交接一次、交接后重读了什么、是否出现压缩、错误诊断如何被纠正、工具调用和修复回合是否重复、首个真实设备操作是否发生、最终义务是否完成。费用从每调用 sidecar 增量读取，不从累积 rollout usage 求和。先指出不确定性和日志缺口，再决定下一轮修改。

## 本轮验证

新增测试覆盖源文件消失后还原、追加与不完整尾行、内容块复用、身份不匹配、完整性破坏及日志故障不阻断 worker。还对历史可用的最后一个修复会话做了实际读写还原，721,423 字节逐字节 SHA256 一致；没有新模型调用。见 [还原审计](audits/native-log-roundtrip-2026-09-26.json)。这不意味着此前缺失的长会话已被恢复。
