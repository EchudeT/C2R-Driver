# 纯 Codex＋goal 提示词

四份预览来自同一个[模板](../../../scripts/native_experiment/baseline-prompt.md)，实际 trial 自动填入真实路径和冻结元数据。不得人工给某一方法补充答案或额外实现建议。

- [VirtIO RNG](virtio-rng.md)
- [VirtIO Block](virtio-blk.md)
- [VirtIO Network](virtio-net.md)
- [NVMe PCI](nvme-pci.md)

每份都要求源语义与真实功能同时对齐，提供完整 Linux 供模型自主调查，并禁止只照测试断言实现。baseline 只使用通用工具与内核上游命令，不提供 Factory 的 build/check、迁移或知识库工具。Factory 可使用自身工具。共同外部评测、计费与隔离规则见[比较协议](../FACTORY_CODEX_COMPARISON.zh-CN.md)。
