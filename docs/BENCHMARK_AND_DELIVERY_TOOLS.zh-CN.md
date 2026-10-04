# Benchmark 验收与开发工具

2026-10-02。对应[Lab 借鉴方案](DRIVER_PORT_LAB_LESSONS_2026-10-02.zh-CN.md)。

交付验收由工作流执行配置的 benchmark 并核对结果。模型审查保持可选，本轮没有新增语义 reviewer。benchmark 用例和断言由使用方后续设计，当前只提供接口及离线验证。

## 新项目配置

```sh
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli port run RUN \
  --source-platform linux --target-platform asterinas --driver-name DRIVER \
  --catalog CONFIG \
  --benchmark /absolute/path/to/benchmark.json \
  --no-analysis-review --no-final-evidence-review
```

新建开发任务默认启用逐行为编码并复用连续会话；`--no-behavior-scheduling` 可建立不启用调度的对照。审查原有默认值未改；上面的两个关闭参数显式选择无模型审查路线。

benchmark 和行为调度设置在创建项目时固定。已有项目不能在恢复时切换这些设置或偷偷更换 benchmark；需要新建运行。当前接入支持 developer-evidence，不改变盲评角色和候选封存协议。

## Benchmark manifest

使用方创建 manifest，例如：

```json
{
  "schema_version": 1,
  "id": "my-driver-benchmark-v1",
  "argv": ["python3", "adapter.py"],
  "cwd": ".",
  "files": ["adapter.py", "tests.json", "oracle.py"],
  "required_cases": ["initialization", "operation", "recovery"],
  "timeout_seconds": 3600,
  "environment": {}
}
```

这些名字只是接口示例，不是为任何驱动新增的固定测试义务。`cwd` 和 `files` 相对 manifest 所在目录解析，命令参数按 argv 原样传递，不经过 shell 插值。启动时解析并固定可执行文件路径及摘要；所有脚本、断言、测试输入和配置都应列入 `files`。文件在运行前后核对，并在每次尝试中归档。应将 benchmark 输入放在候选工作树之外。

命令可调用使用方已有容器/QEMU 测试系统，具体路线由适配器实现。镜像应固定到 digest，工具和外部服务的版本、使用条件由适配器负责校验。控制器不会自动发现所有传递依赖，也不会根据 exit 0 推断实际跑过测试。

控制器向适配器提供：

| 环境变量 | 含义 |
| --- | --- |
| `DPF_TARGET_WORKTREE` | 当前候选目标工作树 |
| `DPF_RUNTIME_ARTIFACT` | 已绑定的 CAS 运行产物 |
| `DPF_BENCHMARK_RESULT` | 本次独有的结果 JSON 输出路径 |
| `DPF_CANDIDATE_IDENTITY` | 本次源码快照、产物、产物身份及 benchmark 配置的联合摘要 |
| `DPF_BENCHMARK_ID` | 冻结的 benchmark ID |

manifest 的 environment 不能覆盖 `DPF_` 变量。适配器执行真实测试、保存实际断言结果后，写入：

```json
{
  "schema_version": 1,
  "benchmark_id": "my-driver-benchmark-v1",
  "candidate_identity": "由 DPF_CANDIDATE_IDENTITY 读取的实际值",
  "cases": [
    {"id": "initialization", "status": "PASS", "assertions": 3},
    {"id": "operation", "status": "PASS", "assertions": 5},
    {"id": "recovery", "status": "PASS", "assertions": 2}
  ]
}
```

`assertions` 是本次实际执行并检查的断言数量，不能把 planned、skip 或编译计作断言。允许状态为 PASS、FAIL、SKIP、NOT_RUN、ERROR；只有完整必测项全部 PASS 且每项断言数大于零才满足结果条件。缺项、多项、重复 ID、旧候选身份、非零退出、超时、输入变化及无效 JSON 都不通过。

适配器是使用方提供的受信任判定器，控制器无法从 JSON 自行证明每条断言的真实性。这不是可让模型上传 PASS 的接口，也不是防恶意同账号进程的隔离系统。

## 工作流完成条件

配置 benchmark 后，在现有交付和可选审查之后增加静态 `benchmark_validation`。所有调用由程序执行，不调用模型判断最终 PASS。关闭两个模型审查不会移除此节点。

通过后保存 `benchmark_report`；失败保存 `benchmark_attempt`，阶段为 FAIL，不能由 checker-decision 的模型接受操作改成通过。完整记录在 `.dpf/benchmark/<attempt>/`，包括冻结输入副本、原始 stdout/stderr、命令及结果。重新打开已完成项目时复核当前候选和验收证据，避免返回过期成功。

失败后先读实际日志并确定需要修复的是代码、产物、环境还是适配器。当前接口保存 FAIL，不自动将所有失败退回驱动编码；按已有阶段重开/返修入口处理。源码修改需重建和重新验收；冻结 benchmark 本身有错则创建修订后的新项目，保留旧失败。

未配置 benchmark 的旧项目继续保留原有公开验证/可选审查结果；其工作者编写的 `public-qemu.sh` 不会自动变成独立 benchmark。没有配置或执行 benchmark 时，不宣称已经完成 benchmark 验收。

## 受控检查工具

交付阶段的 Codex 调用会获得本次任务专用 MCP `driver_checks`，不修改用户全局配置。

- `check({})`：运行已登记 `.dpf-output/experiments.json` 套件；不存在时运行 `public-qemu.sh`。
- `check({"cases":["case-id"]})`：只选择已登记的场景。
- `check({"script":".dpf-output/implementation-smoke.sh","timeout":300})`：调用已有运行脚本。
- `check({"level":"development","script":".dpf-output/build.sh","timeout":120})`：执行开发探针，不产生运行验收。
- `fresh:true`：有意进行新测量；返回 `reused` 区分新执行与复用。

工具一次等待结果，直接返回每项状态、退出/超时信息、receipt 和全部已采集日志路径。复用原 experiment 执行器和跨进程串行锁；CLI 入口保留。公共实验的已有 acknowledgment 规则仍适用。

检查期间不得并行修改/构建同一工作树。取消信号沿执行器清理所属进程组；Docker 等 daemon 管理的资源依然需要脚本保留其正常清理逻辑。开发脚本的成功不等于 QEMU 或 benchmark 通过。工具没有导入 PASS 或改写冻结 benchmark 的操作。

## 按需 guest 诊断

`debug({"capture_after_seconds":30})` 从最近的 managed runtime receipt 寻找唯一的直接 `docker run IMAGE cargo osdk run/test` 命令，核对采集证据和当前源码，定位对应的未剥离 ELF，以记录的镜像 ID 重放 guest 并采集两次 GDB 栈、寄存器和 PC 附近指令。

当前是有意限制的首版：

- shell 包装、自定义 SDK 启动器、多个匹配命令、缺少 bundle/符号/GDB 的路线明确返回不支持或未采集。
- 仅重放 guest，**不重放外部网络对端等 harness 进程**，不能声称复现原测试场景。
- 采样延迟从 QEMU 启动算起，可能采到固件；采样改变时序，不能自动判定死锁。
- 不安装工具、不切换镜像、不自动 fallback。源码已变时需先做正常运行检查，保持 bundle 与当前代码对应。
- 诊断放在 `.dpf/debug/`，不产生验收结果；启动诊断使已有实验/smoke 缓存失效。再次运行正常检查后才能验收。

QEMU 包装器改编自 Lab 的 `adapters/qemu_capture.py`；运行身份、实验锁、日志和清理由 Factory 接入层负责。离线 fake guest 测试只验证采样及进程清理，不冒充真实 QEMU 驱动诊断。

## 行为调度

实现阶段继续接收前序分析形成的契约、测试矩阵、迁移交接、目标平台/API 研究及已有执行路线和决策。完整材料通过原有 `frozen_inputs` 等上下文引用传入，每轮均可查阅。行为计划从这些材料派生；当前项只限定本轮推进范围，不替代分析交接，也不要求重新开展平台和源码分析。

新建开发任务中，首个编码回合通过 `driver_checks.plan` 提议粗粒度行为，工具返回控制器选定的当前项：

```json
{
  "behaviors": [
    {"id":"main-path","outcome":"契约规定的可观察主行为及必要清理",
     "contracts":["已有契约 ID"],"depends_on":[],"constraints":["必要的源条件和目标前提"]}
  ]
}
```

框架适配和必要测试入口随使用它们的当前行为完成，不单列使能或全面探测项目。
当前问题已有证据时不重复探测；构建/启动复用平台工具。每项包含必要生命周期与失败清理；小驱动可以只有一项；依赖环合并为不可分割的单元。合同编号是导航关联，程序不将它当作完整覆盖证明。最终必测项仍由冻结 benchmark 定义。

每轮只推进控制器选定项。复用当前报告，用原提交命令的 `--decision operation --operation behavior_done` 完成当前项，或 `behavior_continue` 继续。普通 pass 不能跳过未完成项。所有行为完成后，准备完整交付并按原协议提交 pass，继续经过源码快照和实际验收。

计划可在当前回合修订；内容变化及相关依赖要求重新核对进度，不删除源码。改了当前边界的旧完成信号不能完成新边界。控制器在交回时保存私有 Git ref，保持 HEAD 和用户 index；进度依据账本事件恢复，`.dpf/behavior-progress.json` 仅用于显示。最多记录 64 次进度交回，达到上限保留现场停止；不借它推断代码错误。

## 验证与尚未建立的结论

新增离线测试覆盖 benchmark 全项验收与审查关闭、缺测/零断言/过期身份、冻结输入变化与禁止模型覆盖、受控工具复用与错误选择、真实本地子进程、行为依赖/修订及实际 runner 交回。合成 QEMU 入口和 fake guest 均明确标记为控制器夹具。

本轮验证：新增专项测试 23 项通过；随后运行 `.venv/bin/python -m pytest -o addopts= -q`，当前工作区全量 300 项通过（480.23 秒）。新增模块及本轮接入文件通过 Ruff F 检查，三份相关文档的本地链接和 `git diff --check` 检查通过。行为调度回归逐轮核对原有契约、测试矩阵、迁移交接及目标研究引用仍在输入中。

尚未接入使用方的真实 benchmark，也没有本方案的真实驱动 benchmark 成功或费用下降记录。受限 Docker/OSDK 诊断路线尚需在适用真实环境复验；不据离线测试宣称运行质量或语义等价。
