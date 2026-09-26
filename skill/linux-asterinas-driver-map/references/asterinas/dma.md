# Asterinas: host DMA

Creation, synchronization, device addresses, and release of host DMA objects. The device address is obtained through `HasDaddr::daddr`; distinguish this from the VirtIO `DmaBuf` trait.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [ostd/src/mm/dma/mod.rs:9–18][C1] | Creating a DMA object requires IRQs enabled; it cannot be created in a hard IRQ, while other operations require separate checks. |
| C2 | [ostd/src/mm/dma/dma_stream.rs:169–199][C2] | `DmaStream::map`: segment/KVA branches, `prepare_dma`, and `map_daddr`. |
| C3 | [ostd/src/mm/dma/dma_stream.rs:201–277][C3] | `sync_from_device/sync_to_device`→`sync_impl`: range, copy, and cache-synchronization order. |
| C4 | [ostd/src/mm/dma/dma_stream.rs:369–389][C4] | Drop→`unprepare_dma`; distinction between `HasPaddr` and `HasDaddr::daddr`. |
| C5 | [ostd/src/mm/dma/util.rs:158–190][C5] | `prepare_dma/unprepare_dma`: actual mapping, protection, and unmapping entry points. |
| C6 | [kernel/core/comps/virtio/src/device/block/device.rs:345–409][C6] | Actual read submission: synchronize request/response, then `add_dma_bufs`, and record `SubmittedRequest`. |
| C7 | [kernel/core/comps/virtio/src/device/block/device.rs:288–338][C7] | On read-request completion, `sync_from_device` then `bio.complete`; read the error-completion branch separately. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | Coherent descriptor/queue memory is used | [ostd/src/mm/dma/dma_coherent.rs][Q1]: check allocation, `HasDaddr`, and Drop; do not interpret coherent as having no publication-order requirement. |
| Q2 | IOMMU, confidential VM, or bounce behavior affects the contract | [ostd/src/mm/dma/util.rs][Q2]: trace the actual `prepare_dma` backend and unprotect/remap dependencies further. |
| Q3 | A device buffer is attached to a VirtQueue | [kernel/core/comps/virtio/src/dma_buf.rs][Q3]: check the `DmaBuf` trait and address/length sources; do not conflate them with the lower-level `HasDaddr`. |

## Verified core paths

1. **Creation**: in a context satisfying [C1], call `DmaStream::map`→`prepare_dma` [C2], [C5]; retain `map_daddr` and provide the device with `HasDaddr::daddr` [C4], not a CPU pointer.
2. **Ownership transfer**: `sync_to_device` / `sync_from_device`→`sync_impl`→conditional copy and cache synchronization [C3]. This transfers ownership between CPU and device; it does not automatically submit a hardware queue entry.
3. **Original read example**: construct a block request and synchronize request/response→`add_dma_bufs`→retain the submitted request [C6]; asynchronous completion takes a request from the used queue→synchronizes in the read direction→`bio.complete` [C7]. Request and response buffers must not be released merely because a stack-local variable went out of scope.
4. **Unmapping**: `DmaStream::drop`→`unprepare_dma` [C4], [C5]. This chain does not guarantee that the device has stopped; the concrete driver remains responsible for late completion after stop/timeout.

## Reading endpoint and boundary

Stop after establishing address source, direction, synchronization range, IRQ restrictions, buffer lifetime, and shutdown preconditions. Architecture-specific cache or IOMMU support needs evidence from the actual backend; do not generalize x86 QEMU success to every platform.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/mm/dma/mod.rs#L9-L18
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/mm/dma/dma_stream.rs#L169-L199
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/mm/dma/dma_stream.rs#L201-L277
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/mm/dma/dma_stream.rs#L369-L389
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/mm/dma/util.rs#L158-L190
[C6]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/device/block/device.rs#L345-L409
[C7]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/device/block/device.rs#L288-L338
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/mm/dma/dma_coherent.rs
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/mm/dma/util.rs
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/dma_buf.rs
