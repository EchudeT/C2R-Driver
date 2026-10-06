# Factory 与纯 Codex＋goal 对照实验

## 实验边界

两组使用相同的完整 Linux、单驱动空心 Asterinas、官方 Docker image ID、
OSDK、QEMU、OVMF、设备、架构、CPU/内存、公开断言和预算。区别是方法及其工具：

| 能力 | 纯 Codex＋goal | Factory |
| --- | --- | --- |
| 标准终端、Git、Docker、OSDK、QEMU | 有 | 有 |
| 完整 Linux 和保留的目标框架 | 有，自主定位代码与上下文 | 有，自主定位代码与上下文 |
| 星绽原生测试源码和固定 SDK/initramfs/磁盘资产 | 有 | 有 |
| 我们编写的 build.sh/check.sh、测试 runner、迁移工具 | 不向模型提供 | 可以使用 |
| 知识检索、责任关联、工作包调度 | 无 | 有 |
| 独立 reviewer | 无 | 本协议关闭 |
| 收尾统一验收和数据采集 | 操作者在模型之外执行 | 操作者读取已有最终批次；没有批次时执行 |

“环境一致”不意味着把 Factory 的工具送给 baseline。Docker 通信的权限隔离与 token
采集属于公共实验基础设施，不向 baseline 增加领域工具、自动代码建议或测试辅助命令。
它仍通过标准 Docker CLI 自主构建、运行原生测试；可以自己编写执行脚本，但不能改断言。
模型之外的评测 runner 使用两组相同的完整用例及构建参数。

baseline 调用 Codex 原生 `thread/goal/set` 建立持久 goal，并保存原生响应；
不是把普通 `codex exec` 改名成 goal，也没有自写“继续修复”的模型提示循环。
Factory 仍使用自身的连续实现机制。两组使用同一个 Codex 可执行文件和版本，
baseline 的 app-server 与 Factory 的 exec 是运行接口差异，记录在协议中。

## 模型、费用与时间

- 服务：`https://ai.wavepulse.live/v1`，Responses API。
- 模型：`gpt-5.6-luna`；默认推理强度 medium。
- 相同默认自动压缩上限 224000，关闭 apps 和多 agent，清空宿主额外 MCP。
  Factory 会按自己的方法附加项目工具；baseline 不附加。
- 每个模型使用新的 `CODEX_HOME`，不继承宿主 Skill、插件、记忆或旧会话。
- 外部共享知识库为空；Factory 本次分析产生的任务知识仍然保留。

本机凭据存放在 Factory 的 `.local/wavepulse.env`，权限 600，Git 忽略；
运行前 `source .local/wavepulse.env`。其他机器设置 `WAVEPULSE_API_KEY` 即可。
提示词、协议、日志说明和版本库不写入密钥。

费用统一按[本地费率表](../../configs/experiments/local-pricing.json)估算，prepare
将这份费率复制进协议。用户可用 `--pricing` 指定自己的表。默认费率是已有本地估算，
不是 WavePulse 实扣价格。缓存输入属于输入的子集：

`USD = ((input - cached) × input_rate + cached × cached_rate + output × output_rate) / 1e6`

不再单独加一次缓存 token 或推理 token。native app-server 使用线程的累计 total
覆盖上次读数，不对累计值反复求和；Factory 使用每次调用已经记录的增量。
失败、修复与格式返工全部纳入。缺失用量记为 unknown/partial；未知缓存写入价格不估成零。

预算在可见的模型回合结束处检查，可能有在途调用超额；两组如实保存实际用量。
时间上限可打断正在运行的翻译。最终公共评测不再调用模型，并保留其实际额外时间。
开始、结束使用 UTC ISO 8601 时间，`total_seconds` 使用单调时钟计算：

- `started_at`：启动实验。
- `translation_ended_at` / `translation_seconds`：翻译执行结束。
- `ended_at` / `total_seconds`：收尾评测和记录结束，包含等待。
- `evaluation_seconds`：收尾评测或读取已有批次的耗时。

不能仅用 Factory 的实现阶段费用与 baseline 全任务费用比较。
环境制作和 prepare 的输入复制不在 run 计时内；共同准备成本单独报告，不能将该时长称为首次搭建环境的总时长。

## 输入与工作区隔离

当前开发机上存在 ground truth 和其他候选，不能把整个宿主文件系统提供给模型。
`run` 使用 bubblewrap 的文件系统和 PID namespace：

- baseline 可见当前 source/、target/、PROMPT.md、公共资产和指定 QEMU 源码。
  看不到顶层 build.sh/check.sh、Factory 源码或 runner；即使 trial 中保留旧兼容入口也不挂载。
- Factory 可见自己的运行工作区、当前单驱动 seed 和必要的框架程序/Skill。
  不挂载 factory 的 Git 历史、docs/audits、其他运行目录或原实现目录。
- 不挂载宿主 HOME、旧 Codex 会话、operator/、其他驱动 seed、其他候选或 Docker socket。
- Docker CLI 通过私有代理到宿主，仅能使用固定 image ID、当前工作区和只读公共资产；
  禁止逃逸符号链接、宿主目录/socket挂载、privileged、host PID/network及操作别人的容器。
- 运行器退出只清理本次实验创建的容器，不清理全局缓存，不复制 Docker/toolchain 备份。

这是对本地工作区和容器挂载的实际隔离，不声称阻止所有恶意渠道。
网络仍用于模型服务和依赖获取；若正式盲评要求技术上阻止下载公开的原 Rust 实现，
需在研究部署层再固定网络出口到模型服务和依赖镜像。提示词禁止取回原实现，但不是网络防火墙。
输入根提交、无 remote/alternates/不可达对象的 seed 审计继续沿用原环境方案。

需要 bubblewrap；系统可安装 `bubblewrap`，本机已有轻量的 `.local/bin/bwrap`。
缺少隔离能力时停止，不自动退回宿主完整权限。

## 提示词审阅

[唯一模板](../../scripts/native_experiment/baseline-prompt.md)由程序填充，实际 `PROMPT.md`
写明当前工作区绝对路径、源入口、目标路径、revision、镜像、架构、设备和公开用例。
四份[提示词预览](baseline-prompts/README.md)便于汇报和逐驱动审阅；预览中的 `/workspace`
是展示路径，真正运行时会替换。

两项要求同时成立：保持冻结范围内的源语义；实现真实功能并通过提供的测试。
不能仅照断言写驱动、硬编码输入/输出、使用 dummy/loopback、伪造成功或把 skip 算通过。
公开测试不是完整规格，也不能证明全部源语义已保持。

## 使用

在 Factory 仓库执行。复用已验证的 suite；不重新 prepare 内核、镜像或工具链。
每次只准备当前要跑的一对 trial，避免提前复制所有完整 Linux 工作树。

```sh
export SUITE="$PWD/../experiments/native-suite-20261004"
source .local/wavepulse.env

# 只准备输入、提示词和共同协议，不调用模型。
.venv/bin/python scripts/compare-experiments.py prepare \
  --root "$SUITE" --driver virtio-rng --trial paper-01 \
  --qemu /path/to/pinned-qemu --max-cost-usd 10 --max-seconds 1800

export PROTOCOL="$SUITE/comparisons/paper-01/virtio-rng/protocol.json"

# 以下才是付费翻译；两组可分别启动，建议交替运行以避免资源竞争。
.venv/bin/python scripts/compare-experiments.py run "$PROTOCOL" --method codex
.venv/bin/python scripts/compare-experiments.py run "$PROTOCOL" --method dpf

# 只读取已有收据和日志，不调用模型、不构建、不重跑测试。
.venv/bin/python scripts/compare-experiments.py collect "$PROTOCOL" --method codex
.venv/bin/python scripts/compare-experiments.py collect "$PROTOCOL" --method dpf
.venv/bin/python scripts/compare-experiments.py export \
  --root "$SUITE" --output comparison.csv
```

新重复实验使用 `paper-02` 等新编号。prepare 拒绝覆盖旧 trial；run 拒绝覆盖旧运行记录。
该入口运行一次完整 trial，不自动续跑失败 trial。人工继续旧任务必须另行记录干预，
不能当作一份新的独立样本；原始记录不删除。建议先试一对 RNG，再固定正式预算。
不同驱动允许预先规定不同预算，但同一驱动两种方法的预算必须一致。

## 输出与统计分母

隔离器保留私有 `/dev`，并仅映射宿主已有的 `/dev/kvm` 与 `/dev/net/tun` 字符设备。
Docker broker 仍只允许这两项设备；不开放宿主 Docker socket、其他工作区或答案目录。
预检应在实际隔离视图中确认设备可见，并经 broker 在固定 Docker 镜像内确认 KVM 可用；
仅在宿主运行 `test -e /dev/kvm` 不足以验证这条执行路径。
若因基础设施错误停止，保留原始失败记录；操作者修复后的原工作区续跑须另外记录干预、
续跑时间和累计用量，不能作为未经干预的新独立样本。

`comparisons/<trial>/<driver>/protocol.json` 是共同配置；下面的 `dpf/`、`codex/`
分别保存 `run.json`、`namespace.json`、`result.json` 和日志。baseline 另有
`goal-state.json`、原生 `events.jsonl`。Factory 原有完整记录仍在自己的 run/.dpf/。

末尾引用同一最终候选的完整公开批次；Factory 已有最终批次就直接读，失败批次也不
被挑选历史 PASS 覆盖。没有批次才由操作者执行一次完整验收。临时路径、报告位置变化
不触发重跑。`collect` 与 `export` 只读证据，缺失或错误结果不会自动返修。

CSV 每次实验一行，包含失败和预算耗尽。旁边的 `comparison.summary.json` 按
方法、驱动、模型及推理强度分组，输出：

- 编译通过率：最终候选构建成功次数／全部已记录尝试，另列 build_unknown。
- 完整测试通过率：完整套件 PASS 次数／全部已记录尝试。
- 单项通过率：通过的固定原生程序/benchmark脚本数／固定应运行数量。
  这里的 case 是测试程序或脚本，不是内部每条断言；不同驱动分别报告，不能直接混池。
- 完整本地费用的均值/中位数、未知/部分费用数量；包含失败样本费用。
- 总时长中位数和原始开始/结束时间；不删除等待以美化结果。

初始空心目标本来就能编译，因此编译成功不能作为实现或语义成功。
程序没有把模型宣布 goal complete 当成测试 PASS，也没有输出“语义等价率”。
独立的语义质量与私有测试需要另行评价。

## 当前验证状态

本次验证为离线/本地基础设施检查：累计 token 与未知费用处理、失败/不完整套件、
同产物证据读取、native goal 协议调用顺序、Docker 越界/符号链接拒绝，以及真实
namespace 隐藏邻近文件和宿主 socket。另验证了隔离后的 Codex 版本与固定 Docker
镜像元数据读取。没有调用付费模型、没有编译内核或跑驱动测试。

goal 接口依据 [OpenAI Docs](https://developers.openai.com/codex/app-server/)
及本机 Codex 0.160.1 生成的协议 schema；自动 goal 翻译的端到端效果尚待真实 trial 验证。
