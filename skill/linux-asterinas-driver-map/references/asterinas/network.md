# Asterinas: network devices

The core is the call contract of `AnyNetworkDevice` and the network component; the VirtIO NIC shows only the original framework integration and buffer lifetime.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [kernel/libs/aster-bigtcp/src/device/mod.rs:31–66][C1] | `AnyNetworkDevice`: `RxPacket`/`TxPacket` ownership, `can_send`, recycling, and `poll_end`. |
| C2 | [kernel/core/comps/network/src/lib.rs:22–66][C2] | `register_device/register_recv_callback/register_send_callback`: device and callback tables, with a `BottomHalfDisabled` lock. |
| C3 | [kernel/core/comps/network/src/lib.rs:68–111][C3] | `handle_rx_softirq/handle_tx_softirq`: call RX callbacks; for TX, recycle first, check `can_send`, then notify. |
| C4 | [kernel/core/comps/virtio/src/device/network/device.rs:77–153][C4] | `NetworkDevice::init`: queue/RX preallocation, transport callback, `finish_init`, and device registration. |
| C5 | [kernel/core/comps/virtio/src/device/network/device.rs:173–236][C5] | `receive/send`: used-length validation, RX replacement, DMA conversion, TX-token retention, and busy semantics. |
| C6 | [kernel/core/comps/virtio/src/device/network/device.rs:311–352][C6] | Trait implementation→concrete send/receive; `free_processed_tx_buffers` recycles tokens; `notify_poll_end` sends notification. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | Preallocation, pool capacity, or exhaustion must be determined | [kernel/core/comps/virtio/src/device/network/buffer.rs][Q1]: check RX/TX pool creation and capacity; read it with packet DMA types. |
| Q2 | It must be proven that the network stack actually uses the new device | [kernel/core/src/net/iface/init.rs][Q2]: inspect upper-layer registration of `register_recv/send_callback` and interface selection; `register_device` alone is insufficient. |
| Q3 | No handling after notification, missed wake-up, or lock context is in question | [kernel/core/comps/softirq/src/lib.rs][Q3]: read softirq init/dispatch; the exact path is in the IRQ entry. |

## Verified core paths

1. **Device initialization example**: `NetworkDevice::init` [C4] creates send/receive queues, pre-fills RX→registers the transport queue callback→`finish_init`→`aster_network::register_device` [C2]. Device registration and creation of an upper-layer network interface are different [Q2].
2. **RX notification and reading are separate**: after a device IRQ, transport calls `handle_recv_event`→`raise_receive_softirq` [C4], [C3]; after softirq scheduling, `handle_rx_softirq` calls registered callbacks [C3], [Q3]. Actual reading uses trait `receive`→concrete receive [C6], [C5], pops used entries→adds a replacement RX buffer→`finish_dma`→removes the VirtIO header.
3. **TX**: trait `send`→concrete send [C6], [C5]→Busy check→`packet.map_dma`→`add_input_bufs`→retain the buffer in `tx_buffers[token]`; when the queue is full, notify and adjust the callback, while `poll_end` can notify again when it is not full [C6]. Submission is not hardware-send completion.
4. **Completion recycling**: TX softirq first calls `free_processed_tx_buffers`→`can_send` [C3]; after the instance pops used entries it clears the `tx_buffers` slot [C6]. Ordinary `send` also attempts recycling [C5], so do not assume that release occurs only in hard-interrupt context.

## Reading endpoint and boundary

Stop after confirming the trait, device registration, upper-layer callbacks, packet ownership, and completion recycling relationship. MTU/offload, hardware RX/TX rings of ordinary NICs, packet loss, and reset still require concrete-driver evidence; this example proves only the VirtIO implementation path.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/libs/aster-bigtcp/src/device/mod.rs#L31-L66
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/network/src/lib.rs#L22-L66
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/network/src/lib.rs#L68-L111
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/device/network/device.rs#L77-L153
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/device/network/device.rs#L173-L236
[C6]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/device/network/device.rs#L311-L352
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/device/network/buffer.rs
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/src/net/iface/init.rs
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/softirq/src/lib.rs
