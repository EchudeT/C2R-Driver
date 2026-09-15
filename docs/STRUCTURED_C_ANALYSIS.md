# 结构化 C 语义事实

`structured_c_analysis` 消费已经通过 `source_closure` 的 compile manifest 和
`compile_commands.json`，不重新猜测编译参数：

```sh
dpf structured-c analyze ./run --analyzer clang
```

当前 `clang-llvm` 后端对每个 translation unit 执行并保存：

- Clang typed AST JSON；
- 带宏定义的预处理输出；
- record-layout dump；
- 带 debug metadata 和 target data layout 的 LLVM IR；
- Clang Static Analyzer CFG dump。

typed AST 会转换为稳定 ID 的 CPG；CFG 会解析为 function/block/predecessor/successor 拓扑；record
layout 会解析为 record 大小、对齐和字段/位字段偏移。预处理输出与 LLVM IR 保留为带摘要和哈希的
`RAW_VALIDATED` 原始证据，不会冒充已经重建完毕的 CPG 事实。

控制器记录 analyzer 二进制、版本与 SHA256，以及每条命令、退出码、stdout/stderr 摘要和原始输出。
它还从 AST JSON 字段构造节点、父子边、声明引用、直接调用目标、函数指针绑定和间接调用候选目标组成的
code-property graph，并索引 function、global、call、control-flow、structural effect candidate 和 source
span。函数指针候选来自 Clang 的 `FunctionToPointerDecay`、结构体/联合体注册表初始化和赋值关系；该过程
只遍历工具结构，不搜索 C 源码文本，也不依据函数名猜测行为。

assignment、call、atomic、volatile typed expression 等只标为结构候选；它们是否属于 MMIO/PIO/DMA、
IRQ、锁、资源所有权或错误恢复，必须在下一阶段结合硬件、source、target 和 QEMU 原文建立合同。
不允许把名称相似或正则命中提升为语义事实。

成功提交不是只检查 JSON 外形。阶段 bundle gate 会在写入 SQLite 前重新验证：

- compile manifest 与 canonical compilation database 的摘要、source revision/tree/clean identity；
- 实际 analyzer executable SHA、target triple、ABI 和完整 argv/cwd；
- compile manifest 与 facts 的 translation unit 一一覆盖，unit ID/source path 不重复；
- 每类 raw fact 的路径、摘要、大小、format、availability、summary 与实际 bytes；
- stdout/stderr 捕获文件与 raw fact bytes 完全相等；
- 从真实 typed AST 重新生成 semantic index，逐字段匹配提交结果；
- unit/top-level semantic counts、report unit count/input/attempt path；
- 所有 repeatable raw/semantic/command 产物恰好被引用，不允许遗漏、跨 unit 交换或额外游离产物。

任一必需工具命令失败或存在 `INDIRECT_UNRESOLVED` 调用时，阶段保持 `RUNNING`，报告指向不可覆盖的
attempt 目录，便于补全 translation unit/编译配置或增强 points-to 分析后重试。只有 AST、CPG、CFG、
布局、预处理、LLVM IR、调用、全局、副作用和 source-span 十个事实域全部导出，且间接调用候选目标
完整时，`migration_contracts` 才能进入 `READY`。
