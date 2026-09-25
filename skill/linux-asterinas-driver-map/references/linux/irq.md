# Linux: interrupts, softirqs, and locks

The generic IRQ registration, dispatch, and release paths; fill in the concrete irqchip, threaded behavior, and driver locks according to the actual configuration.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [include/linux/interrupt.h:170–190][C1] | `request_irq` wrapper: `thread_fn` parameter and common registration entry. |
| C2 | [kernel/irq/manage.c:2124–2190][C2] | `request_threaded_irq`: shared `dev_id`, handler/`thread_fn` validation, `irqaction` creation, and `__setup_irq`. |
| C3 | [kernel/irq/handle.c:185–238][C3] | `__handle_irq_event_percpu`: each `action->handler`; `IRQ_WAKE_THREAD` wakes the thread. |
| C4 | [kernel/irq/manage.c:2007–2029][C4] | `free_irq`→`__free_irq`→release action; `dev_id` must match. |
| C5 | [kernel/irq/manage.c:1929–1964][C5] | Synchronization/thread-stop part of `__free_irq`; do not wait for completion while holding a lock required by the handler. |
| C6 | [drivers/net/ethernet/8390/ne2k-pci.c:436–455][C6] | Real caller: `IRQF_SHARED` registration and `free_irq` after stopping. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | Edge/level IRQ mask/ack/EOI ordering must be confirmed | [kernel/irq/chip.c][Q1]: enter through the current descriptor's flow handler; generic dispatch does not describe device ack. |
| Q2 | The driver actually uses softirq/tasklet or deferred processing is being analyzed | [kernel/softirq.c][Q2]: check budget, re-entry, and scheduling; do not treat threaded IRQ as softirq. |
| Q3 | One lock is shared across contexts | [include/linux/spinlock.h][Q3]: expand the actual irqsave/bh/ordinary-lock macros under the current configuration. |

## Verified core paths

1. **Registration**: driver `request_irq` [C6]→wrapper [C1]→`request_threaded_irq` [C2]→`__setup_irq`. After registration, the handler must tolerate an interrupt arriving immediately; do not postpone required state initialization until after the call returns.
2. **Runtime is an independent hardware entry**: the irqchip/flow handler [Q1] eventually enters action dispatch [C3]→driver handler; `IRQ_WAKE_THREAD` wakes a thread and does not synchronously call `thread_fn`.
3. **Shutdown**: the example first stops the device→`free_irq` [C6], [C4]→`__free_irq` synchronization and thread stop [C5]. Releasing the IRQ does not replace the driver's timer/work/DMA cleanup.
4. **Shared IRQ**: check each handler's ownership test and return value [C3]; a shared-line trigger must not cause access to a stopped device.

## Reading endpoint and boundary

Stop after establishing the actual handler context, device/controller ack responsibilities, shared locks, and shutdown synchronization scope. Expand PREEMPT_RT/forced threading or a specific irqchip only when the task configuration requires it; do not derive them from the ordinary path.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/include/linux/interrupt.h#L170-L190
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/kernel/irq/manage.c#L2124-L2190
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/kernel/irq/handle.c#L185-L238
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/kernel/irq/manage.c#L2007-L2029
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/kernel/irq/manage.c#L1929-L1964
[C6]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/ne2k-pci.c#L436-L455
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/kernel/irq/chip.c
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/kernel/softirq.c
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/include/linux/spinlock.h
