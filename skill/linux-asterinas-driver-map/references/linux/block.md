# Linux: block devices and NVMe

The generic blk-mq dispatch/completion path, plus an NVMe PCI request example; not every Linux block device uses the same submission or completion branch.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [include/linux/blk-mq.h:576–590][C1] | `blk_mq_ops.queue_rq/commit_rqs`: submission interface and batch-tail semantics. |
| C2 | [block/blk-mq.c:2085–2134][C2] | `blk_mq_dispatch_rq_list`: prepare request→`queue_rq`→distinguish `RESOURCE` from error completion. |
| C3 | [drivers/nvme/host/pci.c:2285–2305][C3] | NVMe `mq_ops`: bindings for `queue_rq` / `queue_rqs` / completion, distinguishing batched submission. |
| C4 | [drivers/nvme/host/pci.c:1442–1469][C4] | `nvme_queue_rq`: ready check→prepare→SQ copy→doorbell; submission is not completion. |
| C5 | [drivers/nvme/host/pci.c:1568–1652][C5] | `nvme_irq`→`nvme_poll_cq`→`nvme_handle_cqe`: phase, `dma_rmb`, CID, and batched-completion branches. |
| C6 | [drivers/nvme/host/pci.c:1533–1542][C6] | `nvme_pci_complete_rq/batch`: unmap before handing over to NVMe core completion. |
| C7 | [block/blk-mq.c:1128–1150][C7] | `blk_mq_end_request`→`blk_update_request`→`__blk_mq_end_request`; bio update and request/tag release are different layers. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | Status, retry/ANA/failover, or final block completion must be established | [drivers/nvme/host/core.c][Q1]: trace actual branches from `nvme_complete_rq` / `nvme_end_req`; do not assume every request completes directly and successfully. |
| Q2 | DMA/PRP, timeout/reset, or batched `queue_rqs` is involved | [drivers/nvme/host/pci.c][Q2]: continue into `nvme_prep_rq`, unmapping, timeout, and reset workers; do not read only `queue_rq`. |
| Q3 | The formation of bio→request and scheduling must be explained | [block/blk-mq.c][Q3]: start at `blk_mq_submit_bio` and follow merge/plug/direct-dispatch branches; do not invent one unique submit chain. |

## Verified core paths

1. **Dispatch example**: `blk_mq_dispatch_rq_list`→`q->mq_ops->queue_rq` [C2]; the NVMe binding is in [C3]→`nvme_queue_rq` [C4]→prepare/copy/doorbell. Expand other direct-submission or batched branches through [Q3], [Q2].
2. **Device-completion entry**: `nvme_irq`→`nvme_poll_cq`→`nvme_handle_cqe` [C5]; completion may be deferred or batched, so do not draw it as an always-single-request completion.
3. **Single-request completion**: `nvme_pci_complete_rq`→unmap→`nvme_complete_rq` [C6]; retry/error decisions are in [Q1], and the final block-layer end entry is [C7]. Do not silently assume strategies in the unexpanded core.
4. **Error/queue-full**: dispatch treats `RESOURCE` differently from other errors [C2]; confirm whether request ownership has transferred before returning, to avoid double completion.

## Reading endpoint and boundary

Stop after confirming request ownership, status/retry boundaries, units/capacity, DMA, and completion recycling. Investigate flush/FUA/discard only when the capability is confirmed; successful queue submission does not prove media persistence.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/include/linux/blk-mq.h#L576-L590
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/block/blk-mq.c#L2085-L2134
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/nvme/host/pci.c#L2285-L2305
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/nvme/host/pci.c#L1442-L1469
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/nvme/host/pci.c#L1568-L1652
[C6]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/nvme/host/pci.c#L1533-L1542
[C7]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/block/blk-mq.c#L1128-L1150
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/nvme/host/core.c
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/nvme/host/pci.c
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/block/blk-mq.c
