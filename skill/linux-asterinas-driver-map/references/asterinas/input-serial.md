# Asterinas: input devices and UART

Two independent examples: a PS/2 keyboard and an x86 UART. Replace the front end when the architecture or device protocol differs; do not treat them as generic input hardware.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [kernel/core/comps/i8042/src/lib.rs:1–32][C1] | x86_64 cfg; component init→`controller::init`. |
| C2 | [kernel/core/comps/i8042/src/keyboard.rs:77–93][C2] | Keyboard init: allocate IRQ→`map_isa_pin_to`→`on_active`→register `InputDevice`. |
| C3 | [kernel/core/comps/i8042/src/keyboard.rs:210–230][C3] | `handle_keyboard_input`: `ScancodeInfo::read`→keycode→KEY/SYN events→`submit_events`. |
| C4 | [kernel/core/comps/input/src/input_dev.rs:293–313][C4] | `RegisteredInputDevice::submit_events`: capability assertion, handler list, and `handle_events`. |
| C5 | [kernel/comps/uart/src/arch/x86/mod.rs:23–50][C5] | UART init: existing `SERIAL_PORT`→ISA route→console registration→callback→flush. |
| C6 | [kernel/comps/uart/src/console.rs:41–70][C6] | `trigger_input_callbacks`: batched recv→`VmReader`→console callbacks; `AnyConsoleDevice::send` forwards to Uart. |
| C7 | [kernel/comps/uart/src/console.rs:89–120][C7] | Uart implementation: `Ns16550aUart` send/recv under a lock; the current send adds CR for LF. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | Command ACK, timeout, keyboard/mouse initialization, or failure is relevant | [kernel/core/comps/i8042/src/controller.rs][Q1]: trace actual commands and device initialization from `controller::init`; inspect ownership of data and control ports. |
| Q2 | The device is actually a PS/2 mouse | [kernel/core/comps/i8042/src/mouse.rs][Q2]: investigate `process_byte` packet assembly, sign/overflow, and events; do not reuse the keyboard single-event path. |
| Q3 | Actual FIFO/status/register behavior must be checked | [ostd/src/console/uart_ns16650a.rs][Q3]: trace `Ns16550aUart` send/recv/flush; note the different file-name and type spelling. |
| Q4 | The current task is an RISC-V UART | [kernel/comps/uart/src/arch/riscv/ns16550a.rs][Q4]: investigate FDT/register mapping and IRQ; an x86 ISA pin cannot prove this path works. |

## Verified core paths

1. **Keyboard initialization**: i8042 component→controller init [C1], [Q1]; keyboard routing/callback and `InputDevice` registration are in [C2]. Until the controller command phase has been traced, do not claim that reset/detection protocol is verified.
2. **Keyboard IRQ**: registered `handle_keyboard_input`→`ScancodeInfo::read`→keycode→KEY + SYN array [C3]→`submit_events`→bound `handler.handle_events` [C4]. This is a batch of events, not a raw byte reported directly as one key.
3. **UART initialization and RX**: `arch::init` establishes the ISA route and registers a closure [C5]; asynchronous IRQ→`trigger_input_callbacks`→`Uart.recv`→each console callback [C6], [C7]. Flush occurs after callbacks are set so stale notification conditions are not retained.
4. **UART TX**: `AnyConsoleDevice::send`→`Uart.send` [C6]→lower-level send under a lock [C7], [Q3]; the current LF→CRLF behavior is a semantic difference and must not be mistaken for byte-for-byte transmission. A complete tty/termios contract remains an upper-layer question.

## Reading endpoint and boundary

Stop after explaining event batches, capabilities, input callbacks, and the real controller protocol, or UART byte conversion/FIFO/IRQ boundaries. x86 initialization working does not establish other architectures; a boot log does not validate RX or a complete tty.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/i8042/src/lib.rs#L1-L32
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/i8042/src/keyboard.rs#L77-L93
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/i8042/src/keyboard.rs#L210-L230
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/input/src/input_dev.rs#L293-L313
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/comps/uart/src/arch/x86/mod.rs#L23-L50
[C6]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/comps/uart/src/console.rs#L41-L70
[C7]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/comps/uart/src/console.rs#L89-L120
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/i8042/src/controller.rs
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/i8042/src/mouse.rs
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/console/uart_ns16650a.rs
[Q4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/comps/uart/src/arch/riscv/ns16550a.rs
