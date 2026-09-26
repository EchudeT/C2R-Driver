# Asterinas: PCI/PCIe

The generic PCI enumeration/binding path, with VirtIO PCI used only as an original registration example. Read the framework owner first, then the current device's probe.

[Reference revision and scope](../../SKILL.md). The line ranges below are bound to that official revision; relocate them for other versions. C marks core files for this topic, and Q marks files to open only when the condition holds.

## Core files: read in order

| ID | File and line range | Key symbols, responsibilities, and facts to verify |
|---|---|---|
| C1 | [kernel/core/comps/pci/src/lib.rs:85–131][C1] | `pci_init/init`: architecture initialization, bus/device/function enumeration, creation and registration of `PciCommonDevice`. |
| C2 | [kernel/core/comps/pci/src/bus.rs:18–35][C2] | `PciDriver::probe`: returns `Arc<dyn PciDevice>` on success; returns `PciCommonDevice` on error. |
| C3 | [kernel/core/comps/pci/src/bus.rs:48–95][C3] | `register_driver/register_common_device`: two binding entries, claimed/unclaimed containers, and continuing after an error. |
| C4 | [kernel/core/comps/pci/src/common_device.rs:97–139][C4] | `PciCommonDevice::new`: device identification, command-bit changes before/after BAR sizing, and capability parsing. |
| C5 | [kernel/core/comps/pci/src/cfg_space.rs:237–275][C5] | `Bar/BarAccess` acquisition entry; the I/O acquire TODO is not equivalent to the memory branch. |
| C6 | [kernel/core/comps/virtio/src/transport/pci/mod.rs:17–23][C6] | `virtio_pci_init`: stores the driver in `Once` and registers it through `PCI_BUS`. |
| C7 | [kernel/core/comps/virtio/src/transport/pci/driver.rs:34–69][C7] | `VirtioPciDriver::probe`: vendor/revision matching, modern/legacy transport selection, and retention. |

## Conditional files

| ID | Trigger | File and investigation target |
|---|---|---|
| Q1 | x86 is used or no PCI bus was found | [kernel/core/comps/pci/src/arch/x86/mod.rs][Q1]: trace the current architecture's configuration-space/bus entry; choose the corresponding file under `arch` elsewhere. |
| Q2 | The device uses MSI-X | [kernel/core/comps/pci/src/capability/msix.rs][Q2]: confirm table ownership, configuration, and mask lifetime; this does not prove legacy INTx. |
| Q3 | ID mismatch, configuration-space width, or location is in question | [kernel/core/comps/pci/src/device_info.rs][Q3]: check the range and reads for `PciDeviceLocation` / `PciDeviceId`. |

## Verified core paths

1. **The device appears first**: `pci_init`→`init`→`arch::init`→`PciCommonDevice::new`→`register_common_device` [C1], [C4], [C3]; without a claiming driver it enters `common_devices`.
2. **The driver registers later**: `virtio_pci_init`→`PCI_BUS.register_driver` [C6]→iterate unclaimed devices→`driver.probe` [C3]. The concrete probe branch is in [C7]; do not copy its VirtIO IDs directly.
3. **The driver appears first**: `register_common_device` iterates registered drivers→`probe`, stores a successful device, and takes back a failed device before continuing [C3]. Therefore, do not discard an error-returned object without evidence.
4. **Resource boundary**: `PciCommonDevice::new` adjusts command bits before and after BAR sizing [C4]; BAR acquisition [C5] then splits into memory/PIO. Check who has enabled bus mastering; do not mechanically repeat the Linux enable flow.
5. **Not covered**: this does not prove generic hot-unplug/remove or complete legacy INTx routing. When the task involves them, inspect the current platform originals; do not invent a lifecycle from the probe trait.

## Reading endpoint and boundary

Stop after confirming both binding orders, probe resource ownership, the current BAR/interrupt mode, and initialization order. Expand architecture or capability only when these conclusions cannot be established. An MSI-X example does not prove INTx works for another device.

[C1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/pci/src/lib.rs#L85-L131
[C2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/pci/src/bus.rs#L18-L35
[C3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/pci/src/bus.rs#L48-L95
[C4]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/pci/src/common_device.rs#L97-L139
[C5]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/pci/src/cfg_space.rs#L237-L275
[C6]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/transport/pci/mod.rs#L17-L23
[C7]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/virtio/src/transport/pci/driver.rs#L34-L69
[Q1]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/pci/src/arch/x86/mod.rs
[Q2]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/pci/src/capability/msix.rs
[Q3]: /home/unix/file/C2R-Driver/c2rust-migration-test-01/asterinas/kernel/core/comps/pci/src/device_info.rs
