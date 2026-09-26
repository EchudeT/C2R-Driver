---
name: linux-asterinas-driver-map
description: Provides original-source navigation and review focus for porting Linux C drivers to Asterinas Rust, selected by bus, device type, and mechanism. Use it to narrow platform studies, contract design, or reviews; it does not replace device-specific evidence or the migration workflow.
---

# Linux → Asterinas Driver Map

This material is compiled from the official original Linux and Asterinas kernel projects. It is for source navigation and framework understanding only; it does not represent the current migration's implementation or validated capabilities. Specific conclusions must follow the source revision frozen for the current task.

This is optional source-navigation material. It adds no stage, review call, or delivery document. Select entries for the task first, then open the relevant originals at the currently frozen revision; do not load the whole directory by default. Findings may be merged into the existing platform study, API table, and contract.

## Reference baselines

Only official original Git revisions are used. Local uncommitted changes, ported drivers, experiment patches, and historical run conclusions are excluded. The following were checked through the official repository commit interfaces on 2026-09-22:

- **L / Linux**: [`587858367581b9c55c3690f4e63382ad622719d4`](/home/unix/file/C2R-Driver/c2rust-migration-test-01/linux).
- **A / Asterinas**: [`d4b407ca87de203f79c27c9a83ada1d8223fe280`](/home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas).

These are reference revisions for this map, not mandatory migration revisions, and they are not guaranteed to be the latest stable versions. Line ranges and links in the tables bind to these commits; excerpts are usually entry points rather than complete functions or semantic proofs. With another revision, relocate definitions, callers, and cfg conditions by path and symbol, and cite the complete commit and actual line numbers for the current task. A search with no result means that investigation must continue; it cannot establish that the target lacks a capability.

## Read on demand

Read [Common method](references/common.md) on first use. For source-side analysis select only the Linux column; for target-platform analysis select only the Asterinas column. Read the comparison column when defining mappings or reviewing differences. Do not pre-read all three columns merely to choose a path.

| Trigger | Original Linux | Original Asterinas | Migration comparison |
|---|---|---|---|
| PCI/PCIe | [Linux](references/linux/pci.md) | [Asterinas](references/asterinas/pci.md) | [Comparison](references/pci.md) |
| PIO/MMIO | [Linux](references/linux/io.md) | [Asterinas](references/asterinas/io.md) | [Comparison](references/io.md) |
| Host DMA | [Linux](references/linux/dma.md) | [Asterinas](references/asterinas/dma.md) | [Comparison](references/dma.md) |
| IRQ/shared state | [Linux](references/linux/irq.md) | [Asterinas](references/asterinas/irq.md) | [Comparison](references/irq.md) |
| Ethernet NIC | [Linux](references/linux/network.md) | [Asterinas](references/asterinas/network.md) | [Comparison](references/network.md) |
| Block/NVMe | [Linux](references/linux/block.md) | [Asterinas](references/asterinas/block.md) | [Comparison](references/block.md) |
| VirtIO protocol | [Linux](references/linux/virtio.md) | [Asterinas](references/asterinas/virtio.md) | [Comparison](references/virtio.md) |
| Input/UART | [Linux](references/linux/input-serial.md) | [Asterinas](references/asterinas/input-serial.md) | [Comparison](references/input-serial.md) |

For example, an NE2000 PCI NIC reads PCI + networking + I/O + interrupts; add DMA only when host DMA is actually used, and do not apply the host DMA API merely because device documentation mentions “remote DMA.” A VirtIO PCI block device reads PCI + VirtIO + block-device material, adding DMA and interrupts as required by the mechanisms involved. A regular PCI NIC does not read or implement the VirtIO protocol merely because the reference driver is VirtIO.

There are no dedicated entries yet for USB, GPU, audio, Wi-Fi, I²C/SPI, and so on. Return to the current task's source and general platform study; do not infer “unsupported,” and do not treat a similar-device entry as complete coverage.

## How to read each entry

Each platform entry lists core files C, conditional files Q, verified execution paths, and a reading endpoint in order. First choose the device or architecture branch that actually applies, then read the corresponding C files; expand Q files only when their trigger condition holds. C/Q denotes reading priority, not code quality or permanent importance.

Synchronous calls, callback registration, asynchronous device completion, and worker-thread entry points are described separately in the paths; they are not joined into an unconditional call chain. Example chains cover only the explicitly named original device; when changing drivers, retain framework facts but rebind the device front end. Protocol or architecture branches not yet expanded are explicitly left for investigation and must not be treated as verified.

## Boundaries of use

- Checks in these documents are questions to verify. An original similar driver is a framework example, not a target hardware specification or a defect-free template.
- Core positive conclusions and defects cite current original text; definitions, callers, and configuration are checked separately. Tools can locate line numbers but cannot determine semantic equivalence.
- When the user requires original sources only, read Git objects from the verified official commits; a workspace file at the same path may contain patches. Review the actual migration implementation separately and do not mix it into original-source evidence.
- Reuse evidence verified in this revision; do not copy source, rebuild indexes, or add model reviews for every reading. When the revision, configuration, or related implementation changes, update only affected entries.
