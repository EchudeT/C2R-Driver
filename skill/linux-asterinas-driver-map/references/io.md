# Migration comparison: PIO and MMIO

Read this when the contract, implementation, or review concerns this topic. Platform facts and source line numbers are maintained separately in [Linux](linux/io.md) and [Asterinas](asterinas/io.md); this entry does not copy the source tables.

Compare register space, width, endianness, side effects, caching, and ordering by register group. Bulk copies do not replace single-register access; volatile, locks, and barriers are not interchangeable. Turn address bounds, timeouts, and access after release into explicit invariants.

## Suggested validation

Test legal boundary widths, invalid offsets, odd-length transfers, timeout, and register state after reset. Use ordinary read-back assertions only for registers without side effects. Do not use generic read-modify-write tests for W1C or read-to-clear registers.
