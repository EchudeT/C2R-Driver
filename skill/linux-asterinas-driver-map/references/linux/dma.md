# Linux: host DMA

Focus on host streaming DMA; expand coherent, SG, and IOMMU paths according to actual use.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [include/linux/dma-mapping.h:527–542][C1] | `dma_map_single_attrs`→`dma_map_page_attrs`; rejects `vmalloc`, with an unmap wrapper. |
| C2 | [kernel/dma/mapping.c:153–203][C2] | `dma_map_page_attrs`→`dma_map_phys`; validates direction/mask and selects direct, IOMMU, or `dma_map_ops`. |
| C3 | [kernel/dma/mapping.c:378–410][C3] | `__dma_sync_single_for_cpu/device`: dispatches synchronization to the same class of backend. |
| C4 | [drivers/nvme/host/pci.c:1533–1542][C4] | NVMe completion first calls `nvme_pci_unmap_rq`, then `nvme_complete_rq`; the batched path is separate. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | `dma_map_phys` selects the direct backend | [kernel/dma/direct.c][Q1]: check device-address conversion, mask, and bounce/SWIOTLB branches. |
| Q2 | IOMMU DMA is selected | [drivers/iommu/dma-iommu.c][Q2]: inspect IOVA allocation, mapping, unmapping, and failure cleanup. |
| Q3 | Coherent/SG is used or unmapping must be checked | [kernel/dma/mapping.c][Q3]: continue into the actually used `dma_alloc_attrs`, `dma_map_sg_attrs`, and `dma_unmap_phys` functions. |

## Verified core paths

1. **Mapping**: `dma_map_single_attrs` [C1]→`dma_map_page_attrs`→`dma_map_phys` [C2]→actual backend [Q1], [Q2], or device `dma_map_ops`. A CPU address may become a device address through different paths; one direct experiment cannot be generalized.
2. **Synchronization**: call the sync API at driver ownership-transfer points; backend dispatch is in [C3]. CPU→device and device→CPU are two lifecycle operations, not one `sync_for_cpu` call followed by `sync_for_device`.
3. **Completion/unmapping example**: NVMe completion unmaps before passing the request to completion [C4]; the hardware-completion criterion must come from the concrete queue, and unmapping itself does not wait for the device to stop.
4. **Failure**: when mapping returns an error, do not write `DMA_MAPPING_ERROR` into a descriptor. For partial SG/multi-buffer mapping, unmap only the portions that succeeded; the exact count comes from the current driver.

## Reading endpoint and boundary

Stop after confirming the current map backend, direction, synchronization range, address width, and submission/completion/unmapping responsibilities. Without following IOMMU or non-coherent architectures, do not claim their cache/address behavior is verified; do not apply this to chip-internal remote DMA.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/include/linux/dma-mapping.h#L527-L542
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/kernel/dma/mapping.c#L153-L203
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/kernel/dma/mapping.c#L378-L410
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/nvme/host/pci.c#L1533-L1542
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/kernel/dma/direct.c
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/iommu/dma-iommu.c
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/kernel/dma/mapping.c
