# 面向成功迁移总成本的预处理与早期反馈

日期：2026-09-27。分支：`feat/e1000-run-log-optimization`。在此前 R01–R16 改造上增加能力，不把减少调用数等同于降低总费用。

## 目标与取舍

优先减少事实搜集、错误假设扩散和失去上下文后的命令重建。沿用一个连贯交付任务、独立最终审查和真实公共执行证据。本轮不增加模型节点，不要求新报告格式，不因词法命中、缺少探针或一次命令失败阻断交付。

## 历史证据如何影响实现

读取了 E1000 对话审计、原生快照的可见工具输出及最终独立审查原始报告；未分析隐藏推理。历史运行只读，没有修改其源码、日志或状态，也未启动新付费迁移。

- 对话审计记录目标研究 19 次工具调用中 6 次截断，以及多次重复读取 MSI-X 源码区间。因此增加按主题、域过滤的有界源码导航，而不是把更多源码全文塞进提示。
- 审计记录 host rustfmt 动态库、ktest cwd/firmware/filter、cargo features、隔离 CARGO_HOME 等问题。它们不能简单归类为驱动错误。因此早期探针保存脚本、cwd、输出、声明依赖，并同时保留最近失败和此前成功记录，便于跨上下文恢复命令。
- 最终审查报告中 FER-001 至 FER-005 涉及 RX 边界、失败状态保留、寄存器诊断恢复、发布前 readiness、实际负例设备身份。它们说明纯“有 PASS 标记”不足；本轮增加固定输入对照入口，并在交付指引中要求按当前契约检查边界、失败后的状态和恢复。没有把 E1000 特定长度或寄存器写成通用门禁。

证据来源：

- [历史对话审计](E1000_DIALOGUE_AUDIT_2026-09-26.zh-CN.md)
- [历史审计及原报告路径](../audits/e1000-dialogue-audit-2026-09-26.json)
- 最终审查 CAS：`../e2e-e1000-context-logs-01/.dpf/cas/objects/sha256/8f/7e244771488d276aa15e01c76ea215d34e3301205fce7087699240b16bc6b2`

## 本轮实现

### 1. 翻译事实的来源导航

`knowledge/translation_facts.py` 在分析与交付上下文中提供自动生成材料入口：

- 仅从受控材料清单选取代码，每次查询核验原文件 hash。
- 索引绑定材料清单和提取器版本；命中缓存不重复提取，缓存损坏重新生成。
- 为生命周期、DMA/布局、I/O 顺序、并发、队列、清理、声明提供源码行、片段、版本、hash 和来源。
- 每文件 2 MB、总扫描 24 MB、每文件/主题 16 个位置上限；跳过项和省略数量显式保留。非代码材料继续通过既有知识库检索。
- 自动提示只携带路径、hash、规模及主题；查询默认只返回 12 个位置，避免每轮重新注入全文。

这是真实原文的词法导航，不是 AST、完整调用图、宏展开、条件编译解析或已验证 API 映射。无命中不表示无义务。它为模型提供查阅起点，不替代分析。

```sh
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli knowledge facts RUN \
  --topic dma_layout --domain source --limit 8
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli knowledge facts RUN \
  --topic concurrency --domain target --limit 8
```

历史原文件只读扫描：`e1000_main.c` 149,219 字节，用时约 0.041 秒；`e1000_hw.h` 134,966 字节，用时约 0.035 秒。记录见 [扫描结果](../audits/translation-facts-e1000-2026-09-27.json)。这是单次局部提取测量，不包含全语料 hash 校验和查询开销，也不证明节省了多少 token。

### 2. 任务内早期探针

`migration/probes.py` 与 `experiment probe` 支持分析/交付期间的编译、接口和小型行为探针，无需已经存在可启动 runtime：

```sh
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli experiment probe RUN \
  --script /absolute/run/path/check-api.sh --timeout 120 --contract C-DMA \
  --dependency /absolute/run/path/check-api.rs
```

脚本 cwd 为目标工作树；脚本/依赖参数相对 RUN 或使用绝对路径。通过 `DPF_TARGET_WORKTREE` 获得工作树。原始输出和脚本/声明依赖保存在 `.dpf/probes/`。现有构建工具仍可自行增量编译，本层不假装知道所有外部依赖而自动缓存 arbitrary command。

命令执行与公共实验使用同一互斥锁，避免共享目录同时写入。返回命令成功/失败、超时、输入变化及输出路径，不直接生成阶段 PASS。探针不强制公共 QEMU 容器规则；适用目标容器/工具链由探针脚本按选定路线使用。尚未跟踪所有子进程工具版本或容器身份，不能据此声称完整构建复现。

### 3. 固定输入下的 C/Rust 对照

```sh
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli experiment probe RUN \
  --reference /absolute/run/path/c-reference.sh \
  --script /absolute/run/path/rust-candidate.sh \
  --inputs /absolute/run/path/vectors.txt \
  --dependency /absolute/run/path/reference.c \
  --dependency /absolute/run/path/candidate.rs --contract C-RING
```

两个脚本顺序执行，读取同一份归档 `DPF_PROBE_INPUT`，stdout 为确定性行为输出，构建日志应写 stderr。比较 stdout 原始字节，无任意过滤或屏蔽差异；不一致给出首个差异字节位置。任一命令失败、超时、目标源码/已绑定输入在运行中变化，不报告 MATCH。参考程序失败或修改固定输入时提前结束，不再花时间运行候选。输入和比较输出上限 1 MB，超出则拆分小案例或返回比较限制。

这适用于纯逻辑或明确模型化的可观察行为。MATCH 只覆盖本次输入和所写适配器；原 C 若有未定义行为、适配器失真或输入覆盖不足，不能推出正确性。当前没有自动生成 C 适配器、独立 oracle 或硬件模拟器，也没有用它替代正式公共运行和最终审查。

### 4. 有界诊断与命令记忆

`migration/diagnostics.py` 对早期探针及模型直接调用的 managed experiment 返回：

- 原始 stdout/stderr 路径和 hash；有界尾部与是否截断。
- Cargo/rustc JSON 中的 error/fatal 消息与主位置，去重后最多 8 条。
- 基于真实 launch/timeout/exit 和结构化诊断的类别。普通文本未知失败保持 `COMMAND_FAILURE_UNCLASSIFIED`，不靠关键词自动派发付费修复。

分析/交付交接中保留最近 3 份探针及最多 2 份先前成功记录入口。旧成功仅用于恢复配方，要核对适用性；不是自动复用旧结论。相同输入重复执行给出计数提示，不设置新的拒绝门槛。

### 5. 工作节奏

提示要求在同一任务内按具体驱动依赖推进：先解决会推翻设计的未知点，尽早建立最小行为，再补契约要求的边界、并发和失败恢复。不固定每个驱动的阶段数，不为每个增量新增模型调用，不强制执行所有词法主题对应的探针。

## 验证

- 首批新增 7 项测试通过；新增能力与交付端到端路径的定向回归 33 项通过。
- 实际调用 GCC 和 rustc 比较 ring 算术：正确实现 MATCH，刻意引入取模边界错误后 MISMATCH。这是实际工具链下的纯逻辑测试，不是 E1000 驱动验证。
- 测试包括原文漂移、缓存复用、有界查询、诊断去重、固定输入篡改、源码变化、失败重复、成功配方保留、CLI 依赖归档。
- 全量回归：**197 passed in 206.63s**。随后补充参考失败提前结束和分析交接导航，相关探针/分析回归 **14 passed in 22.64s**（包含新增测试）。当前共 198 项可收集测试；未声称最后又重跑了全部 198 项。
- 所有变更 Python 文件 Ruff F 检查及 `git diff --check` 通过。

## 仍未实现或验证

- 基于编译数据库的语义索引、宏/函数指针解析及自动 API 映射。
- 自动提取并验证原 C 行为适配器、自动生成可靠独立 oracle。
- 跨项目二进制构建缓存、通用 DMA/IRQ 适配组件库。
- 依据实际成功率进行模型自动升级/降级的调度；本轮只有诊断类别，不自动增加模型调用。
- 同环境、同契约、同模型的真实迁移费用/质量实验，以及非网络驱动效果。

下一轮应先用真实运行测量“相关代码定位、首次有效编译/运行、语义返工”的变化，再决定是否扩大预处理和复用范围。不能以本轮工具存在、离线测试通过或单次扫描快，承诺总费用降幅。

后续语义查询、公开输入生成和共享 ccache 的实际范围与成本限制，见 [按需工具实现](ON_DEMAND_TRANSLATION_TOOLS_2026-09-27.zh-CN.md)。这些局部能力不代表完整语义分析或独立 oracle 已实现。
