# Linux: input devices and UART

Two separate examples: PS/2 i8042 input and a non-DMA 8250 UART. Read only the path for the device in this task.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [drivers/input/serio/i8042.c:557–611][C1] | `i8042_handle_data/interrupt`: status/data reads, port selection, and serio dispatch. |
| C2 | [drivers/input/serio/serio.c:961–975][C2] | `serio_interrupt`: calls the currently bound driver's interrupt under the lock; rescans when no driver exists. |
| C3 | [drivers/input/input.c:391–405][C3] | `input_event`: checks event type and enters `input_handle_event`; it does not read hardware. |
| C4 | [drivers/tty/serial/8250/8250_port.c:1816–1878][C4] | `serial8250_handle_irq`→locked helper: RX, modem state, TX branch, and DMA condition. |
| C5 | [drivers/tty/serial/8250/8250_port.c:1678–1692][C5] | `serial8250_rx_chars`: budget 256, repeated `read_char`, and `tty_flip_buffer_push`. |
| C6 | [drivers/tty/serial/8250/8250_port.c:1695–1742][C6] | `serial8250_tx_chars`: `x_char`, stop, FIFO empty, and transmitted-byte count; it does not drain forever. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | The actual binding uses the AT keyboard protocol | [drivers/input/keyboard/atkbd.c][Q1]: trace scan code→key event through the driver's interrupt; do not apply it to a mouse. |
| Q2 | The device is actually a PS/2 mouse | [drivers/input/mouse/psmouse-base.c][Q2]: trace packet assembly, synchronization, and protocol switching. |
| Q3 | The 8250 up→DMA branch is active | [drivers/tty/serial/8250/8250_dma.c][Q3]: the non-DMA RX/TX path is no longer complete; inspect DMA completion and fallback. |
| Q4 | A tty, termios, or power-management contract is needed | [drivers/tty/serial/serial_core.c][Q4]: trace upward through serial core; a minimal console send/receive path is not a complete tty. |

## Verified core paths

1. **PS/2 device entry**: `i8042_interrupt`→`i8042_handle_data`→`serio_interrupt` [C1]→`serio->drv->interrupt` [C2]. The binding selects the downstream keyboard/mouse [Q1], [Q2]; do not write them as one fixed chain.
2. **Event reporting is a protocol-layer call**: after protocol-specific decoding, enter `input_event` [C3]; do not treat one controller byte as one key. Read the corresponding protocol file when a complete scan-code state machine is needed.
3. **8250 RX, non-DMA branch**: `serial8250_handle_irq`→`serial8250_handle_irq_locked` [C4]→`serial8250_rx_chars`→`serial8250_read_char`→`tty_flip_buffer_push` [C5]. First determine whether the RX DMA branch in [Q3] covers the path.
4. **8250 TX**: the same locked handler enters `serial8250_tx_chars` [C4], [C6] when THRE and THRI are enabled and DMA conditions permit; preserve interrupt-disable behavior after FIFO empty/stop.

## Reading endpoint and boundary

Stop after explaining the input-byte/event boundary, IRQ locking, and error handling, or the UART FIFO/error-state/notification recovery. When full tty, mouse, or DMA behavior is needed, promote the conditional entry to core; do not replace it with successful startup of another device type.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/input/serio/i8042.c#L557-L611
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/input/serio/serio.c#L961-L975
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/input/input.c#L391-L405
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/tty/serial/8250/8250_port.c#L1816-L1878
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/tty/serial/8250/8250_port.c#L1678-L1692
[C6]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/tty/serial/8250/8250_port.c#L1695-L1742
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/input/keyboard/atkbd.c
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/input/mouse/psmouse-base.c
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/tty/serial/8250/8250_dma.c
[Q4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/tty/serial/serial_core.c
