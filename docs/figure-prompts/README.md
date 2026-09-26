# 技术报告配图生成说明

为[技术报告](../TECHNICAL_LEADERSHIP_REPORT.md)设计 1 张总览图和 4 张细节图。每份 prompt 均可独立复制到图片生成工具。五张图片已通过用户指定的服务、以 `gpt-image-2` 模型生成，并嵌入正文。使用 imagegen 技能提供的 CLI，未将密钥写入文件。

| 图 | 内容 | Prompt | 建议图片名（放入 docs/figures） |
|---|---|---|---|
| 1 | 三阶段总览与交接产物 | [顶层流程](01-overall-workflow.md) | leadership-01-overall.png |
| 2 | 代码地图、双方框架研究与映射 | [框架理解](02-code-map-and-frameworks.md) | leadership-02-frameworks-v2.png |
| 3 | 目标能力补全与驱动翻译 | [最终简化布局](03-final-layout.md) | leadership-03-translation-v3.png |
| 4 | 程序化检验、单检与双检 | [验收机制](04-layered-verification.md) | leadership-04-verification-v2.png |
| 5 | 按原因局部返修与结果复用 | [修复复用](05-repair-and-reuse.md) | leadership-05-repair.png |

优先制作图 1、2、4，可用最少配图讲清流程、核心方法和质量控制。全部五张完成后，不必再给每个小节添加图片。

## 生成和替换

1. 分别复制对应文件的“可直接复制的 Prompt”，采用建议比例生成高清 PNG。
2. 校对中文、节点和箭头方向。中文生成不准确时，可先生成布局，再用演示文稿或矢量工具补上可编辑文字；不要使用乱码图片。
3. 确认图片缩放到正文宽度时文字清楚，不要用图中长段落替代正文。
4. 如需重新生成，请使用新的版本后缀保存，检查后更新正文图片引用，保留图注。
5. 发布报告时可以删去 prompt 链接，保留生成资料在本目录。

引用示例：

```markdown
![图 2：代码地图与双方框架映射](figures/leadership-02-frameworks-v2.png)
```

五张图均表达工作方法，不承载实验通过状态、性能提升或节费比例。

## 生成记录

- 服务：`https://image.tokenskingdom.com`，模型：`gpt-image-2`，质量：`high`。
- 原始五份提示保留；图 2、4 的最终版本增加了[定向修正](visual-corrections.md)。
- 图 3 首轮及修订版的连接关系不够准确，未用于正文；最终采用[更简洁的单向布局](03-final-layout.md)。
- [批量任务文件](generation-jobs.jsonl)记录首轮图 2–5 的请求参数，不含凭据。后缀 v2/v3 文件是正文选用版本，其他版本保留作过程记录。
- 视觉检查覆盖主要中文标签、流程顺序、来源与目标分析汇合、独立复核的需求依据，以及局部返修的目标节点。
