# v1 本地检索增强生成

2026-10-02。上游 `knowledge-bootstrap.md` 自带词法检索基线，并未强制向量数据库；本次是在其完整性与原文定位契约上增加语义召回。RAG 指检索受控证据并提供给现有生成工作者，不另开一次总结模型调用。

## 实现与调用链

`evidence_closure` 的受控原文 → 按清单校验并切块 → BM25／本地向量索引 → 域／路径过滤 → 倒数排名融合 → 去重及有界原文片段 → 当前工作模型引用原文。生成器仍执行原有迁移任务；检索不会自动认可结论或完成阶段。

- 原有 `knowledge search` 使用 BM25，保留精确标识符，同时拆分 snake_case、CamelCase，中文连续文本增加二字词。
- `knowledge rag` 默认 `auto`：没有向量索引时明确返回BM25；有向量索引则执行混合检索。已存在但过期、损坏或模型不匹配的索引会报错，不自动降级。
- `--mode hybrid` 明确要求语义向量索引；`--mode bm25` 明确要求词法模式。
- 向量来自本地 Sentence Transformers 模型，CPU推理，禁止远程代码和运行时自动下载。长块按模型token窗口编码、归一化均值聚合，避免默默截断尾部；该聚合可能稀释局部语义，仍需真实查询评价。
- 模型文件、库版本、前缀、窗口策略、语料、片段顺序和向量文件均绑定身份。原文每次查询复核；同用户文件哈希不是敌对进程隔离。
- 返回原文、citation ID、路径、精确摘录行号、完整块行号、revision、URL、hash和实际检索模式；默认整个JSON最多12000 UTF-8字节，最多5条证据。预算不够是`BUDGET_OMITTED`，没有词法命中是`NO_MATCH`。
- 向量最近邻总能给出候选，不以分数阈值冒充“答案可信”。必须读原文并判断关联；文件命中不代表句子或行为正确。
- generated KB Skill和`tool_runtime.knowledge_rag`均已接入。只在具体疑点需要时使用，不规定每轮查询或固定检索次数。

未另建MCP、独立向量数据库服务或知识库总结阶段。当前向量表保存在workspace内JSON文件，适合受控小语料；不是大规模ANN服务。

## 配置真实语义模型

在运行DPF的同一个Python环境安装可选依赖：

```sh
.venv/bin/pip install -e '.[rag]'
```

先单独取得固定revision的本地模型目录，记录下载来源、revision和许可证；DPF不会隐式下载。模型应适合查询语言和代码语料。例如本次开发探针用英文 `sentence-transformers/all-MiniLM-L6-v2`，revision为`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`。没有验证中文查询，不能据此声称多语言质量。

新实验在启动controller前设置：

```sh
export DPF_KB_EMBEDDING_MODEL=/absolute/path/to/pinned-local-model
# 模型若需要query:/passage:等前缀，必须依其模型说明明确设置：
# export DPF_KB_QUERY_PREFIX='query: '
# export DPF_KB_DOCUMENT_PREFIX='passage: '
./scripts/run-experiment.sh ...
```

`knowledge_base`阶段将自动构建词法与向量索引，再生成包含hybrid调用的KB Skill。首次完整建库需要读取并编码全部受控文本，成本不能只计查询时间。语料修订后，controller使用相同环境重建。配置模型不可用会失败，不把BM25冒充配置好的语义检索。

采用当前工作流 schema 的**已停止** workspace 可由操作者显式准备向量，不改动原始证据和历史阶段记录。更早版本缺少 target_knowledge_quality 的 workspace 不直接兼容新控制器，须保留旧代码读取或新建实验：

```sh
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli knowledge embed RUN \
  --model-path /absolute/path/to/pinned-local-model
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli knowledge rag RUN \
  --query 'nonblocking acquisition of a mutual exclusion guard' \
  --domain target --mode hybrid --limit 3 --budget 8000
```

旧项目已冻结的生成Skill不会自动改写；新的模型任务通过tool_runtime可访问RAG工具。`embed/rebuild`由操作者或controller负责，工作者只查询。勿在活动实验中切换模型或重建索引。

## 验证记录

1. 新增离线测试使用明确标注的假向量，只验证融合、精确引文、域过滤、预算、过期/损坏拒绝和controller建库配置；不冒充语义质量实验。
2. 实际本地embedding试验读取Asterinas固定提交`d4b407ca87de203f79c27c9a83ada1d8223fe280`中的锁和PCI Git blob，不读取修改中的工作树或旧实验材料。86个片段、384维，6个预先写定的开发问题；BM25文件hit@5为5/6，hybrid为6/6。完整探针约20.0秒，包括模型加载、建库和两种检索；查询用同一encoder实例，不代表每次CLI冷启动延时。
3. 在RCU问题中，BM25把目标文件排在第一位，hybrid排到第四位，说明融合也会让个别问题退步。不能只报告新增命中而忽略排序退化。
4. 改动中的第一轮全量离线测试264项通过；随后增加controller配置回归，最终计数另见提交记录。没有调用生成模型，也没有运行驱动翻译/QEMU；不能声称降低翻译成本或提高最终驱动质量。

结果见[audit JSON](audits/rag-2026-10-02/retrieval-probe.json)。原始packet本机位于`/tmp/dpf-rag-asterinas-01/`，摘要不是完整长期归档。可用以下显式命令重跑，输出目录必须不存在：

```sh
PYTHONPATH=src python scripts/evaluate-rag.py \
  --repository /path/to/asterinas \
  --revision d4b407ca87de203f79c27c9a83ada1d8223fe280 \
  --model-path /path/to/pinned-local-model --output /new/output/path
```

## 与Skill对齐的边界

RAG补的是证据检索与上下文提供；它不能代替上游要求的目标知识质量探针、原文语义判断、缺失资料修复和复测，也不能补出语料根本没有的资料。本轮已在 target study 接入查询重放与原文绑定，工作模型负责语义自检；两项独立 reviewer 在开发路线均可关闭。详见 [执行对齐记录](SKILL_ALIGNMENT_2026-10-02.zh-CN.md)。不能把“向量索引已生成”称作知识质量达标。
