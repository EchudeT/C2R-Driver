# 跨驱动积累的共享知识库

共享库是本地、追加发布的证据库。项目知识库继续负责当前驱动的受控闭包与验收；共享库帮助后续驱动定位已有原文和可迁移经验，不让过去的 PASS 代替当前验证。没有新增模型总结阶段、固定 reviewer 或每轮检索要求。

## 三类记录

- `ORIGINAL`：固定 Git blob 或从任务材料清单导入的原文，保存版本、来源、许可证说明和原始字节。多个驱动可复用相同平台源码，字节按 SHA256 去重。
- `OBSERVATION`：从 developer workspace 导出的公开 QEMU 收据与归档日志，成功与失败都可保留。保留驱动、目标版本及执行来源，不升级为通用语义正确。
- `EXPERIENCE`：问题、解释、适用条件、限制、标签和精确证据引用。只允许引用原文/观察，不允许经验互相引用后“自证”。控制器校验引用身份和行号，不声称解释已经得到证明。

根目录 `objects/<sha256>` 保存原文、记录及快照；`HEAD` 指向最新发布快照。写入使用锁和原子发布，读取不写库。记录撤回后仍能从旧快照审计；新快照查询自动排除被撤回原文及依赖该原文的经验。版本不匹配默认过滤；显式扩大范围得到的内容只能用于发现，必须重新核对。

这是同一用户的本地研究基础设施，不是敌对进程隔离。发布经验是显式操作，不会从模型长对话自动抽取“事实”。没有自动冲突真伪裁决：矛盾经验保留证据，由使用者核对，过期或错误条目可撤回并说明原因。

## 初始真实内容

已在 `../shared-driver-knowledge/` 从固定 Linux 和 Asterinas Git 对象建立初始库，不读取它们修改中的工作树。包括 Asterinas 同步、I/O、PCI、virtio entropy/PCI、编码/安全规范、构建运行材料，以及 Linux pvpanic 和 virtio-rng 源码。统计与快照见 [初始库记录](../audits/shared-knowledge-seed.json)。

初始经验是“保留非阻塞回调获取失败时的提前返回”，引用 Linux 源义务与 Asterinas `try_lock` 原定义，明确标为源码解释。它不是驱动测试结果。初始库不是所有驱动的完整闭包；硬件手册、源框架/测试和 QEMU 证据仍需按实际任务补充。

共享检索当前使用 BM25 和精确版本过滤；任务知识库已有的本地 embedding 混合 RAG 保持原接口。本轮不宣称共享经验向量检索质量已测定，也不宣称目录中的文件数量代表覆盖率。

## 使用

下面命令均从 driver-port-factory 根目录执行；为简洁使用已安装的 `dpf`，未安装时可用 `PYTHONPATH=src .venv/bin/python -m driver_port_factory.cli` 替代。

```sh
# 新任务在 07 资料收集前固定共享库快照，后续建 KB 沿用并写入 query contract。
export DPF_SHARED_KB=/absolute/path/to/shared-driver-knowledge
./scripts/run-experiment.sh ...

# 工作者从当前任务绑定快照按需查询；不是直接追随最新 HEAD。
dpf knowledge shared-search RUN --query 'nonblocking callback try_lock' \
  --platform asterinas --revision FULL_COMMIT

# 07 即可用；控制器按所选仓库自动填入平台与固定版本。
dpf knowledge shared-search RUN --query 'PCI BAR MMIO' --repository target

# 操作者查看共享库，可指定历史 snapshot。
dpf knowledge library status LIBRARY
dpf knowledge library search LIBRARY --query 'PCI BAR' --platform asterinas

# 明确导入固定 Git 原文，不使用工作树内容。
dpf knowledge library import-git LIBRARY --repository /path/to/repo \
  --revision FULL_COMMIT --domain target --platform asterinas \
  --source-url https://github.com/asterinas/asterinas \
  --license-note 'See per-file SPDX and repository license' --file ostd/src/sync/spin.rs

# 实验后保留公开原文和运行观察，不导出独立私有测例。
dpf knowledge library capture LIBRARY RUN
# 发布同一工作报告中已有的有用发现，无需再调用模型写总结。
dpf knowledge library learn LIBRARY experience.json
# 不删除历史：后续快照停用此记录及其依赖经验。
dpf knowledge library retire LIBRARY --entry ENTRY_SHA256 --reason '具体反证或失效原因'
```

工作模型使用 `driver_checks.knowledge_learn`，只填三个字符串：

- `lesson`：可复用的简短经验。
- `conditions`：适用前提和限制。
- `sources`：例如 `target:ostd/src/sync/spin.rs:40-65`，多处位置换行分隔。

不需要模型生成 experience.json、哈希、版本或归档清单。该工具可在分析/实现的现有回合按需调用；
没有发现就不调用，失败不阻塞交付。控制器立即保存所引源码的字节，避免收尾时丢失修复依据；
最终公开验收 PASS 后自动发布经验。源码快照是观察，经验是模型解释，任务通过不证明经验普适。
原有 `library learn` JSON 接口保留给操作者脚本；它不是工作模型交付格式。

默认写回 `DPF_SHARED_KB`。若实验读取隔离副本，可在新任务启动前设置
`DPF_SHARED_KB_PUBLISH=/absolute/path/to/shared-driver-knowledge`，将新发现写入公共库。
读快照和发布位置在首次绑定时保存，恢复运行不随环境变量改变。下一任务应读取公共库，
不能继续复制旧种子并声称复用了新经验。未配置共享库时不自动建立全局路径。

分析入口按驱动及平台固定版本提供最多三条经验，选中内容最多 6000 字节；
其他驱动的机制问题继续通过 `shared-search --query ... --repository target` 按需查询。
使用 `knowledge shared-show RUN --reference REF` 可展开短引用和所引原文。
没有查询配额，也不要求采纳命中。采纳/否决原因写在已有说明中，不新增报告。

`dpf knowledge learning RUN` 展示提供记录、模型发现、发布结果。
提供记录只说明内容被准备给模型，不证明模型采用；采用情况需核对已有报告和代码。
写库失败在结果中保留，验收不撤销；操作者可用 `dpf knowledge learning RUN --publish`
重试已通过任务的发布，幂等入库，不启动模型或测试。

将共享结果用于当前验收前，仍需把相关原始材料通过 evidence_closure 的受控采集纳入当前任务。历史经验只减少定位和重复试错，不跳过版本、调用条件和当前测例。

07 的工作包现在包含共享库初始检索结果（按驱动名、各仓库平台与固定版本过滤）、后续检索命令和本地固定仓库路径。顺序是先共享库导航，再核对本地原文，最后为明确缺口选择外部资料。初始未命中不表示没有资料，仍需按具体问题检索。已有 Git 原文直接选入 `repository_paths`，不重复获取网页副本或探测来源 URL；外部资料由控制器获取、校验并归档。

共享快照在首次进入 07 时记入项目账本，重试、重开知识库、环境变量变化及共享库 HEAD 更新都不会自动换快照；未配置共享库也会记录。旧项目沿用最后接受的 query contract 中的绑定；直接调用建库接口且没有早期绑定时，在建库前固定。选择新的共享快照应新建运行。已启动实验使用的控制器代码副本不会随源码修改自动升级。

若原文已足以支持一个资料限制，可用缺口的 `basis` 引用本次已采集的受控证据项，无需为声明缺口额外联网，例如：

```json
{
  "lane": "hardware",
  "facet": "device_manual",
  "rationale": "所选模型原文支持当前软件设备范围；没有提供物理硬件手册",
  "gap": {
    "impact": "不能据此宣称真实硬件行为已得到验证",
    "repair_trigger": "涉及真实硬件时补充适用的原厂规格",
    "basis": [{"lane": "qemu", "facet": "device_model"}]
  }
}
```

这里要求同一选材方案中存在受控的 `qemu/device_model` 项，且控制器实际采集成功。空引用、未知项及其他缺口不能充当依据；来源权限保持原样，QEMU 原文不会变成硬件手册。程序校验引用和采集结果，不自动证明模型对原文的解释正确，也不证明外部资料绝对不存在。

开发任务允许显式跨驱动积累；正式 held-out/盲评批次应固定共享快照，禁止任务间更新。当前 capture 明确拒绝非 developer-evidence 模式，避免把独立测试反馈混入共享经验。

## 已验证与限制

离线回归覆盖固定原文不受脏工作树影响、重复导入去重、错误引用拒绝、原文损坏拒绝、跨版本筛选、撤回的依赖传播、历史快照保持、失败收据/日志导出及新任务固定共享快照。合成执行不等同于真实驱动验证。

07 提前复用改动的相关回归共 58 项通过，覆盖早期 CLI 查询、真实控制器提交与零 HTTP 采集、跨重启/环境变化的快照保持、后续建库继承、旧项目绑定兼容、无效缺口依据拒绝，以及原有外部文档、目录选材、检索和短引用流程。未启动付费模型或替换正在运行实验的控制器副本；是否减少真实模型冗余操作仍需新运行观察。

共享层尚未提供大规模向量数据库、自动跨版本有效性证明、自动总结晋升或私有评测数据学习。后续实跑应观察经验是否被实际引用、是否减少错误和费用；不能仅用检索次数证明效果。

本次接通工具写回、公共发布位置和下一任务经验入口；尚未开展新版真实模型复用/费用对照。
旧实验09没有新增经验的事实保持不变，不追溯补写它的学习结果。

本轮验证：18项相关离线检查通过；补充短引用原文展开和发布重试CLI检查后，
对应集成用例再次通过。新增知识模块及相关接口通过Ruff检查和格式检查。
这些是合成源码/本地控制器检查，不是真实驱动翻译或降本实验。
