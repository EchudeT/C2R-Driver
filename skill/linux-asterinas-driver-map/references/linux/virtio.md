# Linux: VirtIO devices and transport

The generic VirtIO probe and virtqueue API paths. Read the corresponding device-type entry separately; actual split/packed and PCI/MMIO behavior is selected by negotiation and transport.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [drivers/virtio/virtio.c:270–366][C1] | `virtio_dev_probe`: feature intersection→finalize/validate→`features_ok`→driver probe→`DRIVER_OK`; marks `FAILED` on error. |
| C2 | [drivers/virtio/virtio.c:368–384][C2] | `virtio_dev_remove`: disables configuration notification→driver remove→checks whether reset occurred. |
| C3 | [drivers/virtio/virtio_ring.c:2856–2911][C3] | `virtqueue_add_sgs`→`virtqueue_add`→`VIRTQUEUE_CALL(add)`; out/in direction and format dispatch. |
| C4 | [drivers/virtio/virtio_ring.c:3085–3135][C4] | `virtqueue_kick`→`kick_prepare`→conditional notify; do not notify unconditionally every time. |
| C5 | [drivers/virtio/virtio_ring.c:3154–3175][C5] | `get_buf`→`get_buf_ctx`→`VIRTQUEUE_CALL(get)`; `disable_cb` does not guarantee synchronous callback shutdown. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | The concrete ring format is known | [drivers/virtio/virtio_ring.c][Q1]: trace split/packed add and get paths, descriptors, barriers, and recycling. |
| Q2 | The current device uses PCI transport | [drivers/virtio/virtio_pci_common.c][Q2]: trace queue/config IRQ and notify, then confirm in the modern/legacy implementation. |
| Q3 | The current device uses MMIO transport | [drivers/virtio/virtio_mmio.c][Q3]: trace register width, queue, and interrupt; do not mix it with PCI. |

## Verified core paths

1. **Initialization**: inside `virtio_dev_probe`, finalize/validate features and call `features_ok`, then `drv->probe`, and, when needed, `virtio_device_ready` [C1]. Queue creation belongs to the concrete driver/transport, not to one uniform path here.
2. **Submission**: `virtqueue_add_sgs`→`virtqueue_add`→format dispatch [C3], [Q1]; after success, the caller decides when to `kick`→`kick_prepare`→notify [C4]. Adding a descriptor and notifying are separate API operations.
3. **Recycling**: after device notification, the driver callback calls `get_buf`→`get_buf_ctx`→format dispatch [C5], [Q1] to recover the original buffer using its token. The concrete IRQ callback entry comes from transport [Q2], [Q3], not as the synchronous successor of add.
4. **Removal**: `virtio_dev_remove` expects driver remove to have reset the device [C2]; the asynchronous nature of `disable_cb` [C5] cannot replace stopping the device and waiting for in-flight accesses.

## Reading endpoint and boundary

Stop after verifying the task's feature/ring/transport combination, direction, token, notification, and reset responsibilities. If format-specific implementation remains unexpanded, do not treat this API map as proof that barriers or abnormal-length handling are verified.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/virtio/virtio.c#L270-L366
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/virtio/virtio.c#L368-L384
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/virtio/virtio_ring.c#L2856-L2911
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/virtio/virtio_ring.c#L3085-L3135
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/virtio/virtio_ring.c#L3154-L3175
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/virtio/virtio_ring.c
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/virtio/virtio_pci_common.c
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/virtio/virtio_mmio.c
