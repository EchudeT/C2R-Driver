# Linux: PCI/PCIe

The generic PCI binding path plus an NE2000 front-end example. First confirm the current driver's `pci_driver`; the example does not represent every PCI device.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [include/linux/pci.h:1029–1046][C1] | `struct pci_driver`: match table and probe/remove/PM callback responsibilities. |
| C2 | [drivers/pci/pci-driver.c:1488–1504][C2] | `__pci_register_driver`: sets the bus and hands off to driver core; registration is not successful probe. |
| C3 | [drivers/pci/pci-driver.c:467–518][C3] | `pci_device_probe` / `__pci_device_probe`: platform IRQ allocation, matching, and reference release on failure. |
| C4 | [drivers/pci/pci-driver.c:336–368][C4] | `local_pci_probe`: actually calls the driver probe; error return and runtime-PM state. |
| C5 | [drivers/pci/pci-driver.c:395–451][C5] | `pci_call_probe`: local call or scheduling on the device's NUMA node; do not assume every probe runs in the registering context. |
| C6 | [drivers/net/ethernet/8390/ne2k-pci.c:215–265][C6] | `ne2k_pci_init_one`: enable, BAR0 type, and port acquisition; do not trust the BAR address blindly. |
| C7 | [drivers/net/ethernet/8390/ne2k-pci.c:350–401][C7] | Resource/hardware callbacks→`register_netdev`; error labels release netdev, port, and disable in order. |
| C8 | [drivers/net/ethernet/8390/ne2k-pci.c:684–723][C8] | `ne2k_pci_remove_one`, PM, and the `ne2k_driver` operations table: confirm they bind the example above. |
| C9 | [drivers/pci/pci-driver.c:521–543][C9] | `pci_device_remove`: calls driver remove first, then releases platform IRQ/restores framework state. |
| C10 | [drivers/pci/pci-driver.c:1750–1765][C10] | `pci_bus_type`: generic bus probe/remove are bound to this entry. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | Enumeration result, BAR allocation, or bridge-device behavior is unclear | [drivers/pci/probe.c][Q1]: inspect device discovery/resource reads; do not merge enumeration with driver binding. |
| Q2 | The current device uses MSI/MSI-X | [drivers/pci/msi/api.c][Q2]: check vector allocation mode, fallback, and release; do not read legacy INTx by default. |
| Q3 | Binding timing, deferred probe, or asynchronous behavior must be known | [drivers/base/dd.c][Q3]: follow callers of `bus->probe` through the actual branch; do not assume synchronous binding. |

## Verified core paths

1. **Registration and binding are separate entries**: `__pci_register_driver`→`driver_register` [C2]; the PCI bus probe entry triggered by driver core is `pci_device_probe` [C10], [C3]. Read [Q3] only when driver-core timing matters; do not draw them as an unconditional direct call.
2. **Probe chain**: `pci_device_probe`→`__pci_device_probe`→`pci_match_device` / `pci_call_probe` [C3], [C5]; a local call or work item invokes `local_pci_probe`→`pci_drv->probe` [C4], [C5]. Check the concrete variant selected by ID/`driver_data`.
3. **NE2000 example**: the operations table [C8] points to `ne2k_pci_init_one` [C6]→acquire BAR0 port→install 8390 callbacks→`register_netdev` [C7]. Successful registration still does not mean that open/interrupt processing is running.
4. **Failure/removal**: see the example's goto cleanup in [C7], the bus remove order in [C9], and concrete release in [C8]; verify that each resource is returned exactly once.

## Reading endpoint and boundary

Stop after explaining match scope, BAR type/ownership, probe failure, and remove cleanup. Expand generic driver core for INTx routing, SR-IOV, or PM only when the current contract requires them; without tracing architecture IRQ allocation, do not claim that IRQ routing is confirmed.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/include/linux/pci.h#L1029-L1046
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/pci/pci-driver.c#L1488-L1504
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/pci/pci-driver.c#L467-L518
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/pci/pci-driver.c#L336-L368
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/pci/pci-driver.c#L395-L451
[C6]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/ne2k-pci.c#L215-L265
[C7]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/ne2k-pci.c#L350-L401
[C8]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/net/ethernet/8390/ne2k-pci.c#L684-L723
[C9]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/pci/pci-driver.c#L521-L543
[C10]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/pci/pci-driver.c#L1750-L1765
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/pci/probe.c
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/pci/msi/api.c
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/linux/drivers/base/dd.c
