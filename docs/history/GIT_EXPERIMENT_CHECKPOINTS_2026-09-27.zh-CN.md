# Git 实验检查点与按需冻结源码读取

本轮实现本地实验输入检查点，供后续低成本配对实验使用。没有运行付费模型实验，也没有把日志归档等同于模型会话恢复。

## 已实现的能力

`checkpoint create` 在运行停止时采集当前状态；设置 `DPF_EXPERIMENT_CHECKPOINTS=1` 后，控制器也会在目标研究、契约、框架交付和实现的模型调用前自动采集，包括这些节点上的修复调用。自动采集失败只在该次 metrics 的 `experiment_checkpoint` 中记录原因，不增加翻译重试、模型审查或验收条件；其中 `capture_seconds` 单独记录采集开销。

- 源码：使用临时 Git index 保存当前工作文件的修改、新增、删除、执行位和符号链接；不改变原 HEAD、分支或用户 index。干净基线直接引用原提交。快照提交通过独立 refs 保留，普通 Git GC 不会回收。
- 控制状态：SQLite backup 获取一致的数据库副本，与配置、小型显式材料、原生日志清单一起存入 `.dpf/experiment-checkpoints` 的独立 Git 仓库。该数据库是审计材料，不直接变成新控制器的可运行数据库。
- 大材料：现有 CAS、原生日志块只记录路径、大小、SHA256，不复制一份进 Git；`verify` 和 `fork` 检查这些依赖。必须保留原共享对象库与原源码 Git 对象库。
- 身份：记录采集时间、源码树、工作流代码指纹；自动采集还记录当前模型配置、提示 SHA256、规则指纹和调用原因。指纹用于识别版本，不代替工作流代码本身的版本管理。
- 分叉：从一个检查点创建多个独立源码工作区，Git 对象通过本地 alternates 共享，各分支的工作文件、index 和 refs 独立。默认只检出最后一个仓库（自动发现时是目标工作树）；其他参考仓库按需读取，避免为 A/B 各检出完整 Linux/QEMU。
- 按需读取：`checkpoint read` 直接从冻结 Git blob 返回带行号、内容 hash 和续读位置的有限原文。默认正文预算 6,000 字符，适用于具体问题，既不读取整仓库，也不生成另一个 AI 摘要。

显式 `--source` 可覆盖自动发现的仓库，最后一个被视为默认需要检出的仓库。源码遵守 Git 的忽略规则与常规内容转换规则；被忽略的运行脚本等必要小文件可通过 `--material` 明确保存。忽略目录中的构建产物不被假定已保存。子模块需要另外设计完整采集，当前拒绝把只含 gitlink 的快照宣称为完整源码采集。

## 存储代价

Git 会复用相同 blob，压缩新对象，打包时可对相似内容做 delta。不是每次复制源码仓库、全部日志或构建目录。检查点元数据仓库可手工打包：

```bash
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli checkpoint pack RUN
```

源码提交保存在各自原 Git 对象库，由那些仓库的正常维护负责打包。本命令不维护或清理原仓库。新增/变化源码单文件最多 2 MiB、每仓库合计最多 32 MiB；显式材料单文件最多 2 MiB。这是检查点存储防护，不是驱动质量门槛，自动采集超限不会拦截翻译。

**工作树仍占空间**：检出 A/B 目标源码需要两份可写文件；只是 Git 历史对象不重复。可逐个创建实验工作区、导出所需结果后清理工作区。不得在依赖它的实验仍存在时删除共享对象库或检查点 refs。没有新增自动删除历史材料的策略。

## 使用方式

停止运行后手动采集：

```bash
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli checkpoint create RUN before-delivery
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli checkpoint list RUN
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli checkpoint verify RUN before-delivery
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli checkpoint fork RUN before-delivery /path/to/arm-a
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli checkpoint fork RUN before-delivery /path/to/arm-b
```

自动采集：在启动或恢复控制器的原命令前设置 `DPF_EXPERIMENT_CHECKPOINTS=1`。默认关闭，避免每个普通运行都积累实验资产。自动标签为 `call-<job_id>`，对应调用 metrics 中有索引。不要给模型增加执行 checkpoint 的任务。

若需要保存还未进入 CAS 的报告或被忽略的小型脚本，手动创建时附加 `--material /absolute/path/inside/RUN`，可以重复指定。显式仓库用 `--source /absolute/path/inside/RUN`。

`checkpoint.json` 给出 `sources` 数组顺序，按其索引读取冻结原文：

```bash
PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli checkpoint read RUN before-delivery \
  --source-index 0 --file path/to/driver.c --start 100 --budget 4000
```

输出截断时用 `next.start` 和 `next.column` 传入 `--start`、`--column` 继续读取。需要额外检出仓库时，fork 的 `--source-index N` 可以重复指定。

## 实验适用范围与不能混淆的边界

目前支持 **fresh-task / 新会话交接的局部实验工作区**，不是原生会话 fork，也不是可直接 `port --resume` 的完整迁移项目副本。fork 不复制可续写的会话身份，不继承有效 PASS，不调用模型。归档中的历史项目路径仅用于溯源，不应作为 A/B 的写入路径。

尤其不能把带有原项目路径和提交工具命令的旧 prompt 原样拿去执行：应基于冻结材料编写当前局部任务，明确新的可写目录、所用策略和统一验收终点。需要真实沙箱的实验应另设执行隔离；Git 工作树本身不是访问控制沙箱。参考材料和原日志保留供审计，不默认全部注入新会话。

原生历史连续实验仍未实现；没有声称通过拼接日志恢复隐藏状态或压缩后的精确请求。容器运行状态、外部服务和模型提供方缓存也未冻结。构建工具/镜像路线应引用已冻结的证据，并在配对实验开始前确认实际环境一致。

指向检出源码目录外部的符号链接会使分叉失败，防止实验通过链接修改原目录。失败工作区带有 `INCOMPLETE` 标记，留供诊断，不应拿来执行实验。这不改变原运行的翻译验收。

**不能追溯创建过去的完整检查点**：今天在已完成运行上执行 create，保存的是今天的源码与材料；标签叫 before-delivery 不会令它回到交付前。自动采集用于保证未来实验确实拥有当时的前序输入，避免把后续答案泄漏进早期任务。

## 后续优化如何使用这些资产

优先比较分析完成后的首次完整交付，以及真实缺陷发生后的修复。每次仅改变一项主要策略，双方使用同一 checkpoint、模型配置、冻结义务和验收终点，并明确构建缓存条件。费用计算覆盖分叉后的全部调用和返工；前序已经支付的成本单列，不重复计入 A/B。

比较首次交付是否遗漏必要入口、是否产生真实语义缺陷、总费用、未知用量和完成时间，不能只比较提示体积或某个阶段名称下的价格。先做小范围配对；只有看到收益，才值得再支付完整迁移验证的成本。单次结果不能排除模型随机性。

本轮没有继续新增通用索引、自动测试矩阵或模型评审。已有输入绑定的构建/公共实验复用继续使用，不因为“看起来只改了一个函数”就跳过可能受影响的验证。

## 验证

新增回归覆盖：未提交/暂存内容的正确冻结与原 index 保持、A/B 写入隔离、忽略构建目录、共享 CAS 损坏检测、SQLite 相同 blob 复用、日志清单冻结和日志块不复制、控制器互斥、大文件保护、同一仓库多个工作树的独立 GC pin、失败采集释放 pin、冻结源码的有界续读、默认仅检出目标工作树、非关键采集失败不阻断模型任务。

全量回归 221 项通过，耗时 248.32 秒。随后补充符号链接隔离、元数据异常降级和自动采集成功路径，并对最终版本重跑检查点专项：13 项通过，耗时 0.65 秒。变更 Python 文件 Ruff F 检查、`git diff --check` 通过；CLI 帮助入口已验证。上述均为本地测试，不是付费模型实验或真实驱动质量评估。
