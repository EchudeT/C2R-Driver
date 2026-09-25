# Asterinas: PIO and MMIO

Prioritize investigation of the PIO and `IoMem<Insensitive>` interfaces publicly available to drivers; internal `Sensitive` construction/access is not public API evidence.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [ostd/src/io/io_port/mod.rs:33–53][C1] | `IoPort::acquire/acquire_overlapping`: allocator entry and ownership strategy. |
| C2 | [ostd/src/io/io_port/allocator.rs:25–63][C2] | `IoPortAllocator::acquire/recycle`: `checked_add`, overlap check, and occupied/released ranges. |
| C3 | [ostd/src/io/io_port/mod.rs:96–121][C3] | `read/write`→architecture `PortRead/PortWrite`; Drop recycles the owned port range. |
| C4 | [ostd/src/io/io_mem/mod.rs:216–234][C4] | `IoMem<Insensitive>::acquire`→`acquire_with_cache_policy`→allocator, default `Uncacheable`. |
| C5 | [ostd/src/io/io_mem/mod.rs:287–311][C5] | `VmIoOnce for IoMem<Insensitive>`: one read/write after range and alignment checks. |
| C6 | [kernel/core/comps/virtio/src/transport/mod.rs:152–209][C6] | `ConfigManager::read_once/write_once`: real modern `SafePtr` and legacy BAR call sites. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | Acquire failure or a reserved-range/initialization question | [ostd/src/io/io_port/allocator.rs][Q1]: inspect how init excludes sensitive ranges; a private `new` does not establish that the public interface is unavailable. |
| Q2 | MMIO acquisition, mapping, or recycling fails | [ostd/src/io/io_mem/allocator.rs][Q2]: check acquire/ownership management and cache-policy propagation. |
| Q3 | The task uses x86 PIO | [ostd/src/arch/x86/device/io_port.rs][Q3]: trace `PortRead/PortWrite` instructions; use the corresponding architecture file elsewhere and do not generalize x86 port semantics. |

## Verified core paths

1. **PIO lifetime**: `IoPort::acquire` [C1]→`allocator.acquire` [C2]→owned typed port; `read/write`→architecture `PortRead/PortWrite` [C3], [Q3]; Drop→`recycle` [C3], [C2]. Port ownership and device reset are separate responsibilities.
2. **MMIO acquisition and access**: acquire→`acquire_with_cache_policy`→allocator [C4], [Q2]; then `VmIoOnce.read_once/write_once` [C5] performs a single access after range/alignment checks. These are two API stages; acquire does not automatically read a register.
3. **Original caller example**: `ConfigManager` selects modern/legacy [C6]; modern uses `SafePtr`, legacy uses a BAR interface. A same-named `read_once` does not justify crossing types or visibility to call an internal `Sensitive` method.
4. **Checkpoints**: verify PIO width/overlapping ranges, MMIO alignment/offset, W1C and read-to-clear behavior, and device ordering separately; ordinary byte copies cannot replace width-sensitive register access.

## Reading endpoint and boundary

Stop after following the public acquisition entry to the actual access implementation and confirming type, visibility, range, and releaser. Investigate internal `Sensitive` extensions only for a concrete gap; do not conflate volatile access, memory barriers, and device-protocol ordering guarantees.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/io/io_port/mod.rs#L33-L53
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/io/io_port/allocator.rs#L25-L63
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/io/io_port/mod.rs#L96-L121
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/io/io_mem/mod.rs#L216-L234
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/io/io_mem/mod.rs#L287-L311
[C6]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/transport/mod.rs#L152-L209
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/io/io_port/allocator.rs
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/io/io_mem/allocator.rs
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/ostd/src/arch/x86/device/io_port.rs
