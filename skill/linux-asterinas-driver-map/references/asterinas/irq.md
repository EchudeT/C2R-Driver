# Asterinas: interrupts, softirqs, and locks

Distinguish hardware routing, the OSTD top/bottom halves, kernel softirq, and lock guards; they are different mechanisms.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [ostd/src/irq/mod.rs:90–102][C1] | `call_irq_callback_functions`: after entering interrupt level, runs `top_half::process` and then `bottom_half::process`. |
| C2 | [ostd/src/irq/top_half.rs:41–96][C2] | `IrqLine::alloc/alloc_specific/on_active`: allocates and retains callback handles; this is not ISA/PCI hardware routing. |
| C3 | [ostd/src/irq/top_half.rs:177–194][C3] | CallbackHandle Drop removes the callback; process calls every callback under a read lock and then `hw_irq_line.ack`. |
| C4 | [ostd/src/irq/bottom_half.rs:42–80][C4] | `process` selects an L1/L2 handler by interrupt level; context changes require reading the guard handling. |
| C5 | [kernel/core/comps/softirq/src/lib.rs:140–153][C5] | Component init registers `process_pending` as the L1 bottom-half handler. |
| C6 | [kernel/core/comps/softirq/src/lib.rs:171–229][C6] | `process_pending/process_all_pending`: pending/enabled masks, budget, and daemon deferral. |
| C7 | [kernel/core/comps/softirq/src/lib.rs:296–312][C7] | `run_callbacks`: selects a line from the action mask and calls its retained callback. |
| C8 | [ostd/src/sync/spin.rs:27–75][C8] | Default `PreemptDisabled`; `disable_irq` changes the guard, while an ordinary lock does not automatically disable IRQs. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | An original ISA IRQ-routing example is needed | [kernel/comps/uart/src/arch/x86/mod.rs][Q1]: after `IrqLine::alloc`, follow `map_isa_pin_to` and then `on_active`; an ISA pin is not directly a vector. |
| Q2 | The device uses MSI-X | [kernel/core/comps/pci/src/capability/msix.rs][Q2]: inspect device-table configuration and IRQ-resource ownership, and check the concrete transport separately. |
| Q3 | `BottomHalfDisabled` or re-entry/lock inversion is involved | [kernel/core/comps/softirq/src/lock.rs][Q3]: check guard toggling, release behavior, and call order. |

## Verified core paths

1. **Registration does not trigger handling**: allocate `IrqLine`→establish the actual route [Q1] or current-device path→`on_active` [C2]; retain the owning handle so it is not destroyed immediately after registration.
2. **Hard-interrupt entry**: `call_irq_callback_functions` [C1]→`top_half::process` [C3]→device callback→controller ack. Device-state ack/mask remains the device handler's responsibility; it cannot be replaced with `hw_irq_line.ack`.
3. **Deferred stage**: the same OSTD entry then calls `bottom_half::process` [C1], [C4]. Kernel init registers L1 `process_pending` [C5]→`process_all_pending`→`run_callbacks` [C6], [C7]; when the budget is reached or the CPU should yield, work is deferred to the daemon rather than always completed immediately.
4. **Release and concurrency**: process runs under the callback-list read lock, while CallbackHandle Drop obtains the write lock to remove an entry [C3]. Check actual relationships for destruction while holding the lock or from inside a callback; Drop alone does not prove shutdown is deadlock-free.
5. **Shared state**: use [C8] to distinguish preemption disabling from IRQ disabling. Re-prove lock ordering for state shared by process and hard-interrupt contexts; check softirq-guard behavior in [Q3].

## Reading endpoint and boundary

Stop after establishing pin/vector routing, execution context, device versus controller ack, lock type, deferred handling, and destruction synchronization. The IRQ registration API alone does not prove that the current device can generate interrupts; without expanding the CPU trap front end, do not claim that the whole architecture chain was audited.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/irq/mod.rs#L90-L102
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/irq/top_half.rs#L41-L96
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/irq/top_half.rs#L177-L194
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/irq/bottom_half.rs#L42-L80
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/softirq/src/lib.rs#L140-L153
[C6]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/softirq/src/lib.rs#L171-L229
[C7]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/softirq/src/lib.rs#L296-L312
[C8]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/sync/spin.rs#L27-L75
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/comps/uart/src/arch/x86/mod.rs
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/pci/src/capability/msix.rs
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/softirq/src/lock.rs
