# 由控制器提供的平台执行层

本轮解决的问题：环境阶段只验证 QEMU 设备模型，后续工作模型仍要重新搭建 Docker
构建、内核启动、QMP 和控制台链路。驱动很小，也会因缓存挂载、启动参数、socket
路径等问题耗费大量时间。平台执行层把这些工作转为固定工具，模型保留驱动适配及
设备特定刺激、断言的责任。不增加 reviewer，也不增加必需的独立模型阶段。

## 当前实现与适用范围

已实现 Asterinas x86_64、官方 dev image、GRUB ISO、initramfs、串口 shell 的适配器。
运行时必须显式选择 `kvm` 或 `tcg`，没有自动 fallback。已实测的路线是 KVM；TCG
入口不表示该目标版本在 TCG 下能成功启动，验证失败就不能通过环境阶段。
其他平台继续原显式环境路线，不能将本次实现描述成已支持所有内核或所有启动方式。

新建 Asterinas 项目默认 `managed_platform=true`。历史配置缺少字段时解释为 false，
原冻结实验不被改写。新的环境验收入口要求平台基线验证通过；单纯设备模型 smoke
不能代替目标内核构建和启动。环境阶段仍需完成原来的设备证据检查。

| 工具承担 | 模型承担 |
|---|---|
| 官方镜像身份、固定构建命令和挂载、持久工具缓存 | 驱动、必要框架适配、初始化/构建接线 |
| 统一日志级别/控制台的 ISO 构建及身份记录 | 依据源行为和测试要求选择设备参数 |
| QMP 握手与启动、串口交互、事件收集 | 设备特定刺激、结果断言及合理的负例 |
| 短 socket 路径、时限、QEMU/容器清理 | 根据具体原始观察诊断功能问题 |
| 源码/制品/配置变更检测，保留每次失败 | 解释哪些义务已验证及哪些仍有限制 |

## 操作接口

在活动的 environment_recovery 阶段准备并验证：

```sh
dpf environment platform prepare RUN
dpf environment platform verify RUN
```

镜像必须已在本机，工具不会 pull。prepare 记录配置而不声明通过；verify 必须使用
未修改的目标基线，实际构建 ISO，建立 QMP 会话、启动内核、等待 shell，并执行一个
随机标记的 shell 命令。标记在命令中拆分，避免把终端输入回显误判为执行结果。
KVM 的实际访问发生在容器内：prepare 检查宿主字符设备存在，不用控制器用户的读写权限
误判 Docker 权限；verify 的真实启动才能证明路线可用。验证失败仍留在环境阶段，保留收据，
不允许把未完成的启动链路推给驱动实现。

实现阶段调用：

```sh
dpf environment platform build RUN
dpf environment platform run-case RUN .dpf-output/harness/shutdown.json
dpf environment platform status RUN
```

验证后控制器生成 `.dpf-output/harness/platform/build.sh`、`run-case.sh` 和默认
`check-presence.sh`。模型可在现有 `implementation-smoke.sh`、`public-qemu.sh` 调用
run-case，并通过原 managed check 工具运行；无需自己编写 QMP client 或 Docker 命令。
主 CLI 使用运行控制器的 Python/安装位置，不能把这些本地主机入口移入容器。

build 使用同一 worktree 增量构建，成功后发布 `.dpf-output/runtime-artifact`。
失败的新构建撤销当前可用 build 指针，保留历史结果。presence 验证制品与当前构建、
源码和配置的关联；它不替代驱动功能验证。run-case 接受 `DPF_RUNTIME_ARTIFACT`，
以相同绝对路径只读挂载外部 CAS 制品，兼容现有运行制品身份采集。

设备测试 JSON 示例（仅说明格式，不自动成为某个驱动的验收标准）：

```json
{
  "devices": ["pvpanic-pci,events=7"],
  "timeout_seconds": 120,
  "steps": [
    {"wait_serial": "# "},
    {"send_serial": "poweroff -f\n"},
    {"wait_event": "GUEST_PVSHUTDOWN"},
    {"wait_event": "SHUTDOWN"}
  ]
}
```

支持 `wait_serial`、`send_serial`、`qmp`、`wait_event`、`observe_seconds` 和
`assert_no_event`。负例必须提供真实刺激及有依据的观察窗口，单独的“没有事件”不证明
驱动语义正确。超时不是成功。复杂设备协议可以在既有测试工具中实现；当前声明式接口
不声称覆盖所有外设操作。case JSON 放在 harness 目录中，参与现有检查身份绑定。

## 配置与证据边界

- 配置绑定目标/QEMU 基线版本、host architecture、镜像 ID、加速方式、构建参数及适配器
  代码身份。profile 和验证报告由控制器登记到 CAS/账本，模型不能通过提交文字 PASS 配置环境。
- 工具缓存使用 Docker named volume，由镜像内容初始化；不会用空主机目录覆盖 Cargo
  安装目录。默认按项目、目标版本和镜像隔离。源 worktree 的 target 目录保留增量产物。
- 构建网络和运行网络分开固定：本适配器构建使用 host，运行使用 none。
  不因运行无需网络而自动禁用构建依赖访问。
- QMP 地址使用容器 `/tmp` 下的短路径；串口、QMP 响应/事件、stderr 增量落盘。
  失败和取消会清理本工具启动的 QEMU/容器，不清理其他实验。
- 基线验证、后续构建和测试都保留独立收据。最新失败验证不能被较早 PASS 遮盖；
  源码、制品或配置不匹配时不能运行旧候选。执行工具变化也使既有检查缓存失效。
- `BASELINE_BUILD_BOOT_VERIFIED` 仅说明环境能构建和运行目标内核。
  `CASE_OBSERVED` 仅说明声明的步骤执行及断言通过。它们都不是驱动最终验收，仍需
  原实现 smoke、公开验收与所配置 benchmark。审查仍按用户配置启停。

平台适配代码跨驱动复用，不要求每个模型重新发明执行框架。当前不跨项目共享编译
目录或直接继承基线 PASS；每个项目执行当前验证。后续如增加共享缓存，仍须保留
版本/配置绑定及候选的实际运行，不把历史成功当作当前候选通过。

## 验证记录

2026-10-03 在独立干净 checkout、目标 commit
`d924a9635a66c7c3bb43e563eaafa2c61d6ee9d5`、官方镜像
`asterinas/dev:0.18.1-20260805` 下进行本地验证，无付费模型调用：

- 首次基线构建 225.390 秒，包含首次 named-volume 初始化和编译；ISO 生成成功。
- 首次启动已进入 shell，但检查期待 `/ # `，实际提示符为 `~ # `，120 秒后失败。
  保留该失败；改为等待 shell 标志并验证随机命令实际输出，不改驱动/内核。
- 修正后的 KVM QMP/串口/命令回读验证通过，容器调用 20.894 秒。
- 再通过原 `run_public_harness` 验证集成：实际 QEMU、官方镜像 ID、制品绑定和运行日志
  均被采集，机械门禁通过。该测试仍是未迁移驱动的基线平台测试。

离线测试覆盖进程超时/退出清理、长工作区路径、配置边界、修改证据、最新失败遮蔽旧成功、
源码/镜像/制品漂移及环境阶段禁止用设备 smoke 绕过基线验证。
真实记录见 [platform-execution-2026-10-03](../audits/platform-execution-2026-10-03/summary.json)。
没有修改当前冻结 pvpanic 实验；也没有据此声称其整次翻译成本已经降低。

论文评价中应让各方法使用相同平台工具，并分别报告准备与后续迁移成本。
这些工具解决实验基础设施重复劳动，不应单独冒充语义翻译方法的创新。

## 2026-10-03：实际 CLI 接入缺陷与修复

干净 pvpanic 实验暴露了测试缺口：`environment platform prepare/verify` 与
`environment prepare-smoke` 在实际命令入口报出
`scoped integrity is restricted to read-only consumers`，尚未执行 Docker。
原因是这些需要写入产物/收据的入口使用 `open_project(..., verify_artifacts=False)`，
与控制器只允许只读消费者使用局部校验的规则冲突。平台经验导入/导出存在同类错误。

修复保留 `open_project` 的保护条件：写命令使用默认的完整账本/CAS 校验；平台和平台经验
`status` 明确只读。没有跳过摘要校验，没有导入人工 PASS，也没有用手工 Docker 运行结果
补造受控平台验证收据。完整校验会增加本地读取工作，当前优先确保执行入口正确。

环境提示词同时明确：必需控制器工具在执行前出现内部完整性/权限策略/分派错误时，保留命令
和错误并报告基础设施阻塞；不能通过重写构建、Docker 或 QMP 包装继续替代受控路线。
真实模型/驱动断言失败仍按原有局部修复规则处理。

`tests/test_platform_cli.py` 通过真实参数解析、项目重开、服务和账本覆盖：

- prepare → verify 失败 → verify 重试成功 → build → presence → run-case；失败收据保留，
  源码变动后旧制品/运行不可接受，平台成功不把环境阶段或驱动置为 PASS。
- 实际 `python -m driver_port_factory.cli` 子进程调用平台 prepare/status、prepare-smoke
  及平台经验 export/import/status，覆盖此次失败的真实入口；仅 Docker 镜像查询由本地桩替代。
- 三类写入口遇到损坏 CAS 必须在执行/写入服务前拒绝，证明不是通过放松完整性要求解决。

上述是离线控制器测试，构建/启动为明确的合成执行器，不是新的真实驱动验证。
之前真实基线 Docker/KVM 构建启动记录仍只证明平台执行器路线；它不能替代本次 CLI 回归。
当前实验使用的冻结控制器不被热更新；修复适用于后续新建运行。

修复验证：全量 pytest 361 项通过；CLI/环境/平台/只读边界专项 25 项通过。
[结果与日志](../audits/platform-cli-2026-10-03/summary.json)保存测试范围及限制。

环境执行已完成[采集重构与 OVMF 兼容修复](OVMF_AND_EXECUTION_REFACTOR.zh-CN.md)：托管容器内跟踪替代短命进程轮询，基础设施故障不进入付费返修。OVMF 的 pvpanic BAR 兼容补丁作为显式本地派生镜像提供，原 QEMU 与目标内核不变。真实平台检查通过，端到端驱动成本仍待新实验。
