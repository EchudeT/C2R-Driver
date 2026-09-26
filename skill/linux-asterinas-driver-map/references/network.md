# Migration comparison: network devices

Read this when the contract, implementation, or review concerns this topic. Platform facts and source line numbers are maintained separately in [Linux](linux/network.md) and [Asterinas](asterinas/network.md); this entry does not copy the source tables.

Map skb/NAPI ownership, backpressure, budget, and wake-up guarantees to the target packet, poll, and softirq paths. A poll method with the same name does not prove equivalent semantics; RX/TX rings, minimum frames, FCS, overflow, and reset still depend on the concrete hardware contract.

## Suggested validation

Test bidirectional packet content and length, recovery after sustained TX backpressure, RX wraparound, bad frames, overflow recovery, and traffic after stop/reset. `ping` is one end-to-end scenario and cannot alone cover the whole contract.
