# 科研汇报配图

对应[科研汇报](../../research/FACTORY_RESEARCH_REPORT.zh-CN.md)。本轮先按用户要求使用已有图片；语义对齐图随后按用户要求从头重新生成，未编辑旧图。选中图片复制到报告的 `figures/` 目录，报告不依赖本地临时输出目录。

| 图 | 报告图片 | 原始文件（相对仓库根目录） | 提示词 |
| --- | --- | --- | --- |
| 1 总体架构 | [图片](../../research/figures/01-architecture.jpeg) | `output/imagegen/56758fea-67e3-4884-af86-b42c617751f6.jpeg` | 根目录新增图片，具体生成请求未记录；[原始架构规格](01-architecture.txt)供对照 |
| 2 语义对齐 | [图片](../../research/figures/02-semantic-alignment.png) | `output/imagegen/research-20261004/03-semantic-alignment-redraw.png` | [全新绘制提示词](03-semantic-alignment-redraw.txt) |
| 3 局部修订 | [图片](../../research/figures/03-local-revision.png) | `output/imagegen/research-20261004/04-local-revision-original-1k.png` | [提示词](04-local-revision.txt) |
| 4 知识传递 | [图片](../../research/figures/04-knowledge-flow.png) | `output/imagegen/research-20261004/02-knowledge-flow-original-1k.png` | [提示词](02-knowledge-flow.txt) |
| 5 知识消融 | [图片](../../research/figures/05-knowledge-ablation.png) | `output/imagegen/research-20261004/05-knowledge-ablation-original-1k.png` | [提示词](05-knowledge-ablation.txt) |

## 当前状态

这些是汇报插图草稿，不是已完成审校的论文定稿。图 1 已按用户指定改用 JPEG 版本，正文保留准确流程定义供核对。图 4 的义务来源及快照读取连线仍待修正，报告图注明确给出正确关系。语义对齐图已重新生成并核对主要连线，去掉中点分隔；消融图的中点分隔留待后续替换。保留正文的准确流程图及机制解释，不以生成图片定义系统行为。

虚线框中的统一发现索引为计划项；消融图展示实验设计，没有测量结果。图 3 的 R1/B1、R2/B2、R3/B3 是说明局部依赖的示例，不意味着每个驱动强制拆成三个工作包。

## 生成来源与后续替换

`research-20261004` 下所选生成图使用用户指定服务 `https://image.tokenskingdom.com/v1` 的 `gpt-image-2`，经 imagegen 技能 CLI 生成，原有图请求为 high、1024×1024；语义对齐重绘图使用 generate（无输入图片），请求为 high、1536×1024，实际返回也是 1536×1024；文件名中的 1k 表示请求档位，实际返回像素可能不同。根目录新增图的具体请求参数未记录，不推断其模型或尺寸配置。

密钥不得写入文档、提示词或 Git。报告只收录选中版本，原始候选保留在本地输出目录。后续可替换报告图片并同步更新本表，不需要改变报告正文结构。
