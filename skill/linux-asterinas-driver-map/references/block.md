# Migration comparison: block devices and NVMe

Read this when the contract, implementation, or review concerns this topic. Platform facts and source line numbers are maintained separately in [Linux](linux/block.md) and [Asterinas](asterinas/block.md); this entry does not copy the source tables.

Map request/tag submission, cancellation, and exactly-once completion to the `SubmittedBio` lifecycle; normalize bytes, sectors, and logical blocks first. Ordinary write completion does not replace the persistence guarantee of flush/FUA; retain features only when applicable and justified.

## Suggested validation

Test boundary LBAs, cross-page and multi-segment I/O, read-back of known content, a full queue, device errors, timeouts, and late completion. Design assertions for flush and similar features only when those capabilities are agreed. Distinguish a test data disk from a boot disk according to the existing run configuration.
