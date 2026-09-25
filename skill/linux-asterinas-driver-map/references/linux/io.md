# Linux: PIO and MMIO

PIO and MMIO are separate branches. This entry gives generic MMIO access and an NE2000 PIO data-port example; the applicable architecture may override the generic implementation.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [include/asm-generic/io.h:222–236][C1] | `readl`: `__io_br`→`__raw_readl`→little-endian conversion→`__io_ar`. |
| C2 | [include/asm-generic/io.h:286–298][C2] | `writel`: pre-write barrier, endianness conversion, raw write, and post-write handling. |
| C3 | [drivers/net/ethernet/8390/ne2k-pci.c:529–574][C3] | `ne2k_pci_block_input`: remote-DMA registers, `insl/insw`, and trailing bytes. |
| C4 | [drivers/net/ethernet/8390/ne2k-pci.c:576–655][C4] | `ne2k_pci_block_output`: length adjustment, `outsl/outsw`, RDC polling, and timeout recovery. |
| C5 | [drivers/net/ethernet/8390/ne2k-pci.c:461–488][C5] | `ne2k_pci_reset_8390`: reset reads/writes and bounded reset confirmation. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | The target source configuration is x86 or actual I/O instructions must be confirmed | [arch/x86/include/asm/io.h][Q1]: check in/out and read/write macros, access width, and clobbers; use the corresponding path for another architecture. |
| Q2 | `request_region` conflict or asymmetric resource release is in question | [kernel/resource.c][Q2]: investigate request/release ranges and parent resources; do not equate port acquisition with MMIO mapping. |
| Q3 | The driver uses `ioread/iowrite` interfaces | [lib/iomap.c][Q3]: confirm I/O-token space discrimination and the corresponding access; do not directly apply `readl`. |

## Verified core paths

1. **MMIO**: after the current driver calls `readl/writel`, first determine the architecture selection [Q1]. Apply the call sequence in [C1], [C2] only when the generic branch is actually used; this is macro/inline dispatch, not one identical function chain on every architecture.
2. **PIO RX example**: `ne2k_pci_block_input` [C3] sets remote count/address→starts chip remote read→bulk-reads from the data port; preserve the odd trailing byte and transfer-width branches. This remote DMA is not host-DMA mapping.
3. **PIO TX example**: `ne2k_pci_block_output` [C4] adjusts count→sets registers→writes the data port→waits for RDC; timeout calls reset and `NS8390_init`. Check every length-adjustment branch for sufficient source-buffer data; do not copy only `outsw`.
4. **Reset**: the reset-port read, write-back, and status polling in [C5] form a hardware-operation sequence; arbitrary read-modify-write cannot replace it.

## Reading endpoint and boundary

For the registers actually used, record width, endianness, side effects, ordering, and timeout, then stop after confirming resource boundaries. Cache policy, posted writes, and device W1C behavior still require architecture and device evidence; generic source alone cannot prove them.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/include/asm-generic/io.h#L222-L236
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/include/asm-generic/io.h#L286-L298
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/ne2k-pci.c#L529-L574
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/ne2k-pci.c#L576-L655
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/ne2k-pci.c#L461-L488
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/arch/x86/include/asm/io.h
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/kernel/resource.c
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/lib/iomap.c
