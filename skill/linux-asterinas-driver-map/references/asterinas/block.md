# Asterinas: block devices and NVMe

The generic Bio/BlockDevice contract, plus the original VirtIO block worker and completion paths. Pay special attention to who actually consumes the software queue after registration.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [kernel/core/comps/block/src/lib.rs:61–90][C1] | `BlockDevice::enqueue/metadata`: submission interface and capacity/segment constraints. |
| C2 | [kernel/core/comps/block/src/bio.rs:110–150][C2] | `Bio::submit`: Init→Submit, creates `SubmittedBio`; rolls state back when enqueue fails and joins `IoBatch` on success. |
| C3 | [kernel/core/comps/block/src/request_queue.rs:52–106][C3] | `BioRequestSingleQueue::enqueue/dequeue`: merging, segment-count check, wake-up, and waiting. |
| C4 | [kernel/core/comps/virtio/src/device/block/device.rs:85–150][C4] | `BlockDevice::init/handle_requests/enqueue`: registration, software queueing, and read/write/flush dispatch. |
| C5 | [kernel/core/src/device/registry/block.rs:26–61][C5] | `init_in_first_kthread`: starts a consumer thread for the concrete VirtIO/NVMe type, then scans partitions. |
| C6 | [kernel/core/comps/virtio/src/device/block/device.rs:345–409][C6] | `DeviceInner::read`: request/response synchronization, capacity wait, `add_dma_bufs`, notification, and token retention. |
| C7 | [kernel/core/comps/virtio/src/device/block/device.rs:267–338][C7] | Registered queue callback→`handle_irq`: status, read-direction DMA synchronization, and error/success completion. |
| C8 | [kernel/core/comps/block/src/bio.rs:239–270][C8] | `SubmittedBio::complete`: consumes `self`, releases segments, invokes the callback, publishes state, and wakes the waiter. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | The device has partitions or a capacity-offset issue | [kernel/core/comps/block/src/partition.rs][Q1]: confirm partition LBA conversion and lower-layer enqueue; do not confuse device sectors with partition sectors. |
| Q2 | The actual migration is to NVMe | [kernel/core/comps/nvme/src/device/block_device.rs][Q2]: switch to the NVMe consumer/queue path; do not treat a VirtIO descriptor as an NVMe command. |
| Q3 | DMA pools, segment sizes, or wait return values are involved | [kernel/core/comps/block/src/bio.rs][Q3]: expand `BioSegment`, `IoBatch`, and related constructors; submission and completion use different memory objects. |

## Verified core paths

1. **Submission thread**: `Bio::submit`→`BlockDevice::enqueue` [C2], [C1]; VirtIO implementation→software `queue.enqueue` [C4], [C3]. This may only queue the request; it has not necessarily written hardware.
2. **The consumer thread is a separate entry point**: for an identified type, `init_in_first_kthread` creates a loop [C5]→`handle_requests`→`queue.dequeue`→`DeviceInner.read/write/flush` [C4], [C3]; the actual hardware submission for read is in [C6]. A new driver reusing this queue must confirm that something consumes it; implementing the trait and calling `register` is insufficient.
3. **Asynchronous completion**: the queue callback registered during initialization→`DeviceInner.handle_irq` [C7]→response status/read-direction synchronization→`SubmittedBio.complete` [C8]. Completion is published after the callback returns; one request may complete only once.
4. **Boundary/failure**: the current `queue.enqueue` rejects `segments.len() >= max` [C3]. Check the limit declared by metadata against the actual boundary rather than copying it as a universal correctness rule. Distinguish a full queue, error status, late completion, and reset from requests that were accepted or not accepted.

## Reading endpoint and boundary

Stop after finding the real consumer, hardware submitter, and completer, together with byte/sector units and state/ownership boundaries. The original concrete-type dispatch is an integration constraint clue, not automatic permission to change the framework; first verify the smallest integration plan for the current task.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/block/src/lib.rs#L61-L90
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/block/src/bio.rs#L110-L150
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/block/src/request_queue.rs#L52-L106
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/device/block/device.rs#L85-L150
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/src/device/registry/block.rs#L26-L61
[C6]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/device/block/device.rs#L345-L409
[C7]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/device/block/device.rs#L267-L338
[C8]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/block/src/bio.rs#L239-L270
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/block/src/partition.rs
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/nvme/src/device/block_device.rs
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/block/src/bio.rs
