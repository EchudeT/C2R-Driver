# Asterinas: VirtIO devices and transport

Read the component state machine and queue first, then expand the actual PCI/MMIO transport. Device-type handling is outside the generic queue layer.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [kernel/core/comps/virtio/src/lib.rs:43–123][C1] | `virtio_component_init`: transport init, reset, feature negotiation, `FEATURES_OK`, and device-init dispatch. |
| C2 | [kernel/core/comps/virtio/src/lib.rs:125–151][C2] | `pop_device_transport/negotiate_features`: obtain PCI/MMIO devices and select features by `device_type`. |
| C3 | [kernel/core/comps/virtio/src/transport/mod.rs:34–90][C3] | `VirtioTransport`: responsibility for configuration, state, queues, and callbacks. |
| C4 | [kernel/core/comps/virtio/src/queue.rs:271–361][C4] | `add_dma_bufs`: does not set WRITE for input and sets WRITE for output; descriptor chain and avail publication. |
| C5 | [kernel/core/comps/virtio/src/queue.rs:411–455][C5] | `pop_used_with_min_bytes`: used index, token/length checks, recycling; continues after abnormal entries. |
| C6 | [kernel/core/comps/virtio/src/device/network/device.rs:124–153][C6] | Original device caller: `register_queue_callback`→`finish_init`→conditional notify→subsystem registration. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | Modern PCI | [kernel/core/comps/virtio/src/transport/pci/device.rs][Q1]: inspect capability/BAR, queue addresses, MSI-X, and callback; do not apply legacy widths. |
| Q2 | Legacy PCI | [kernel/core/comps/virtio/src/transport/pci/legacy.rs][Q2]: inspect old register layout, PIO, and notification; actual probe selection decides. |
| Q3 | MMIO transport | [kernel/core/comps/virtio/src/transport/mmio/device.rs][Q3]: check queue/IRQ configuration and device state; PCI material does not cover it. |
| Q4 | The device reports an abnormal used entry or recycling is lost | [kernel/core/comps/virtio/src/queue.rs][Q4]: continue into `recycle_descriptors`, `can_pop`, callback control, and notify; check whether abnormal entries affect recycling/progress. |

## Verified core paths

1. **Initialization**: `virtio_component_init`→`transport::init`→`pop_device_transport` [C1], [C2]; reset→ACKNOWLEDGE/DRIVER→negotiation→non-legacy `FEATURES_OK` confirmation→device init by type [C1]. The device side completes `DRIVER_OK` through `finish_init`; see [C6].
2. **Data-submission API**: `add_dma_bufs` [C4] builds descriptors from its arguments. Here inputs are device reads and outputs are device writes; do not map the English “in” in Linux `add_inbuf` directly. The caller then decides `should_notify/notify` [C6].
3. **Completion API**: a device callback or poll enters `pop_used_with_min_bytes` [C5], reads token/length, and recycles only a valid entry; an invalid entry is skipped, so continue checking concrete device error recovery and resource progress [Q4].
4. **Transport boundary**: concrete implementations of the [C3] trait are selected by [Q1], [Q2], and [Q3]. Seeing one unified trait does not prove packed rings, every feature, or every architecture works.

## Reading endpoint and boundary

Stop after confirming the current negotiation result, ring/transport implementation, device-initialization completion point, direction, token/length, and notification recovery. Infinite reset polling, unwraps, and abnormal recycling remain task-specific review questions; original behavior is not a defect-free specification.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/lib.rs#L43-L123
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/lib.rs#L125-L151
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/transport/mod.rs#L34-L90
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/queue.rs#L271-L361
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/queue.rs#L411-L455
[C6]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/device/network/device.rs#L124-L153
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/transport/pci/device.rs
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/transport/pci/legacy.rs
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/transport/mmio/device.rs
[Q4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/queue.rs
