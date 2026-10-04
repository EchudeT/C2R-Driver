# 按需语义查询、公开测试输入与编译缓存

## 成本约束先于工具扩展

本轮新增功能不调用模型，不添加流程节点或报告要求，不自动执行生成的测试。详细使用说明移出通用 job 提示：只有分析/交付阶段获得短命令入口和可选文档路径，不向采集、范围确认或最终审查自动注入新工具材料。工具用于解答具体问题，不要求模型逐个使用。

默认语义查询只返回指定符号的 8 个位置；公开输入默认最多 12 个，优先显式关键案例，之后变化单个字段的边界。不展开笛卡尔积，不追求把每个函数测完。生成预算不足不改变项目状态，也不自动转成模型修复。契约确实需要更多案例时，才明确扩大范围或分批执行。

## 1. 语义查询：已实现的范围

`knowledge/semantic.py` 使用实际编译数据库的参数解析一个 C/C++ 翻译单元，保留宏和配置影响，查询指定名字的声明、字段类型、变量或成员引用与位置。

```sh
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli knowledge semantic RUN \
  --compile-db /absolute/run/path/compile_commands.json \
  --file /absolute/run/path/driver.c --symbol descriptor --limit 8
```

每次先预处理并解析当前头文件依赖；预处理输出和依赖 hash 相同才复用 AST 提取结果。因此不仅修改旧头文件会失效，新增加的高优先级同名头文件也会使结果更新。记录 Clang 版本和规则身份。AST 保存在磁盘，不传给模型；每条类型显示最多 300 字符，结果数最大 30。超大 AST 返回 SIZE_LIMIT；编译配置不支持返回 UNAVAILABLE 和诊断，不判定驱动错误。

**不是“完整语义索引已经完成”。** 当前不支持 Rust 语义索引、全程序调用图、函数指针目标推导、字节级结构布局和锁正确性证明。仅接受编译数据库中的直接编译器命令；wrapper、response file 需先整理。不自动改 GCC 专用参数以制造 Clang 成功，必要时回到原文和真实构建。复杂宏位置仍应结合原文核实。

## 2. 自动公开测试输入：已实现的范围

`migration/source_cases.py` 按显式输入范围和受控原材料行号生成确定性输入；它不读取 Rust 候选代码。

```json
{
  "contract": "C-RING",
  "evidence": [{"path": "controlled/original.c", "line_start": 10, "line_end": 20}],
  "integer_fields": {"index": {"min": 0, "max": 7}},
  "critical_cases": [{"index": 7}]
}
```

```sh
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli experiment generate-cases RUN \
  --spec /absolute/run/path/source-domain.json --budget 12
```

结果文件的 `cases` 数组供上一轮的 C/Rust 对照适配器共同读取，保存 spec、生成器、原材料的 hash。关键案例优先，范围中点与单字段边界按预算补充，并显示省略数量。当前仅支持最多 8 个整数字段，不自动猜测输入范围、函数调用协议或硬件刺激。

**不是自动独立 oracle 或盲评系统。** 输入范围由作者从原材料确定，引用验证只证明位置和版本，不证明语义。当前工作上下文已看过候选实现，不能声称独立测试者身份。该功能是公开开发证据；真实预期仍需原 C 的有效定义行为或经审查的规范 oracle。未引入额外 AI 生成测试/审查测试调用，更不会把生成文件当作测试 PASS。

## 3. 跨项目 ccache：已实现的范围

`build_cache.py` 根据目标版本、架构、工具链和配置建立显式共享 namespace。相同身份的项目可复用 ccache 编译对象；实际源码、头文件与编译参数匹配由 ccache 负责，不自行猜测任意构建的依赖。

```json
{"architecture":"x86_64","toolchain":"pinned-gcc-or-clang","configuration":"explicit-build-config"}
```

```sh
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli build-cache configure RUN \
  --store /absolute/shared/compiler-cache --profile /absolute/run/path/cache-profile.json
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli build-cache status RUN
```

只在开发证据模式启用。配置使用 compiler_check=content、空 sloppiness、hash_dir=true，每个 namespace 默认 5 GB；不会复用 runtime、测试 PASS 或其他项目结论。探针自动携带已配置环境；交付模型只在启用后看到环境入口。已有 ccache wrapper 或显式 `ccache gcc/clang` 才能实际命中。容器需要显式挂载与转发；没有自动改容器路线。缓存配置损坏或不适用时自动回退普通编译，不阻断阶段或触发付费修复；status 显示是否仍有配置文件。保留目录/调试信息的正确性比盲目提高命中率优先。

当前不包含 Cargo/Rust 编译对象、内核镜像缓存，不承诺 E1000 主体构建会因此明显加速。`status` 是共享 namespace 累计计数，不是单项目节省。没有真实成本证据前不为更高缓存命中率重构构建系统。

## 验证记录

新增离线测试覆盖实际 Clang 类型提取与头文件失效、索引失败提示、有界输入、候选代码不影响生成结果、原材料引用限制、真实 ccache 命中和配置篡改检查。首轮 6 项通过；随后补充按阶段投放入口和同名头文件遮蔽回归。

全量回归 **204 passed in 224.68s**。新增按阶段投放与头文件遮蔽测试、调整提示后，交付/策略专项 **43 passed in 86.10s**；最后修改缓存损坏时回退行为后，相关工具/规则专项 **9 passed in 13.23s**。当前共 206 项可收集测试，未宣称最后再次重跑全部 206 项。所有变更 Python 文件 Ruff F 检查、关键错误静态检查和 `git diff --check` 通过。

全部为本地测试，没有付费模型调用或新一轮真实 E1000 迁移。真实缓存测试在两个构建目录间观察到 ccache 命中；尚未进行多个真实驱动工程间的缓存收益对照。

## 后续是否扩展的判据

只有真实记录表明模型反复查找某类关系、某类错误持续漏检或某类构建消耗显著时，才扩展对应工具。衡量成功迁移的总费用、必要缺陷发现和修复成本；不以索引规模、生成用例数、缓存命中率或测试数量本身作为目标。
