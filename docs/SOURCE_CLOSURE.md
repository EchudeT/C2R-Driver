# C 源码闭包门

`source_closure` 对应原 `knowledge-guided-driver-port` 的 workflow 第 3 阶段和
`translation.md` 的 Required source facts。Codex 可分析候选闭包，但只有静态验证器可令阶段通过。

```sh
dpf source-closure validate ./run --closure ./run/source-closure.json
```

输入契约见 `schemas/source-closure.schema.json`。它必须固定 source revision、编译器族/文件/版本、
ABI、语言模式、显式 defines、include paths、配置/生成头、条件分支、每个 translation unit 的
完整 argv、文件摘要和依赖，并逐项处理 shared core、header、宏/配置、callback/function pointer、
registration table、source test 与 framework contract 七类闭包。

当前执行后端支持 `gcc-compatible` 编译器族（GCC 与 Clang 风格参数）。验证器不会只相信模型列出的
依赖：它会使用冻结 argv 执行编译器 `-MM` 依赖扫描，漏掉源树内头文件即失败；同时核对真实编译器
版本与文件摘要、source/argv 关联、`-std`、`-D` 和 `-I`。其他编译器族应增加独立适配器，不能伪装
成 GCC 兼容族。

通过后产生：

- 原闭包与静态验证报告；
- `compile_commands.json` 及带编译器身份的 compile manifest；
- 继承 acquisition 语料的不可变 successor corpus manifest；
- 绑定 parent/successor digest、新增 source Git blob 和 `READY` 索引身份的 knowledge revision。

失败尝试进入独立、不可覆盖的 attempt 目录，阶段保持 `RUNNING`。知识索引重建等后置操作失败后，
允许用同一份闭包重试。bundle gate 会拒绝父语料被修改或重排、伪造的 successor digest、非冻结
source Git blob，以及与新增记录不一致的索引。只有全部产物原子登记后，`structured_c_analysis` 才
变为 `READY`。
