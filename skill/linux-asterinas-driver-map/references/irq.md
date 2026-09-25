# Migration comparison: interrupts, softirqs, and locks

Read this when the contract, implementation, or review concerns this topic. Platform facts and source line numbers are maintained separately in [Linux](linux/irq.md) and [Asterinas](asterinas/irq.md); this entry does not copy the source tables.

Compare execution context, lock scope, notification, and in-flight callback synchronization one by one. Do not transfer the guarantee of Linux `free_irq` directly to Rust `Drop`; validate device ack/mask separately from controller EOI.

## Suggested validation

Test bursts, shared or unrelated interrupts, completion/shutdown races, empty-to-nonempty queue boundaries, repeated notifications, and interrupt recovery after reset. Lock-order checks complement stress tests; one deadlock-free startup cannot replace concurrency analysis.
