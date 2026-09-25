# Target-Platform Profile

## Identity

- Target platform:
- Source root:
- Revision:
- Artifact mode:
- Profile status: `DRAFT | READY | STALE`

## Repository and documentation map

| Area | Original path/URL | Revision | Lines/pages | Authority | Notes |
| --- | --- | --- | --- | --- | --- |

## Closest analogous implementation

- Candidate comparison:
- Selected implementation and why:
- Registration-to-cleanup trace:
- Relevant framework owners:
- Differences that must not be copied:

## Lifecycle and execution contexts

| Callback/state | Trigger/context | May block/allocate | Lock/IRQ rules | Ownership/cleanup | Evidence |
| --- | --- | --- | --- | --- | --- |

## Target API evidence

| API/type | Driver purpose | Definition | Call site/example | Errors | Safety/lifetime | Status |
| --- | --- | --- | --- | --- | --- | --- |

## Hardware access and concurrency

- Resource discovery:
- MMIO/PIO access:
- DMA/buffer ownership:
- Interrupt/deferred work:
- Locks, guards, atomics and ordering:

## Error, recovery and observability conventions

- Error types and propagation:
- Retry/drop/reset/detach behavior:
- Logging, counters and runtime markers:

## Coding and safety requirements

- Formatting/naming:
- Architecture boundaries:
- Safe Rust expectations:
- `unsafe` requirements:
- Documentation/review rules:

## Artifact and QEMU path

- Base artifact identity:
- Driver inclusion mechanism:
- Integration files:
- Final artifact identity method:
- QEMU runner/arguments:
- Driver-presence proof:

## Target changes and unresolved gaps

| Item | Evidence | Alternatives | Proposed action | Risk | Status |
| --- | --- | --- | --- | --- | --- |
