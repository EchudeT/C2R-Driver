# Migration comparison: PCI/PCIe

Read this when the contract, implementation, or review concerns this topic. Platform facts and source line numbers are maintained separately in [Linux](linux/pci.md) and [Asterinas](asterinas/pci.md); this entry does not copy the source tables.

Keep match scope and variants consistent; rebuild ownership for resource acquisition and probe failure from the target call chain. A Linux interrupt line is not directly an OSTD IRQ vector. Map each source lifecycle step to a call path the target actually supports.

## Suggested validation

An incorrect ID must not claim the device; invalid or missing BARs must not cause out-of-bounds access; a mid-acquisition failure must reclaim resources; device interrupts must route to the processor through the current architecture; and no released resource may be accessed after shutdown. Require dynamic hotplug or power-management tests only when the task includes those features.
