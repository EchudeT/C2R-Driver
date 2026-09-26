# Migration comparison: host DMA

Read this when the contract, implementation, or review concerns this topic. Platform facts and source line numbers are maintained separately in [Linux](linux/dma.md) and [Asterinas](asterinas/dma.md); this entry does not copy the source tables.

Map by direction, address space, CPU/device ownership transfer, and buffer lifetime rather than replacing API names. A source-side location that can be allocated atomically does not prove that the target can create a DMA object; NE2000 remote DMA is not automatically host DMA.

## Suggested validation

Test bidirectional data integrity, segmentation and boundaries, allocation failure, queue reuse, and late completion after timeout reset. Claim non-coherent/IOMMU behavior only after it has actually run; success on x86 QEMU cannot prove synchronization on every architecture.
