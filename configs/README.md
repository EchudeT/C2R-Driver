# Driver catalogs

`configs/drivers/` contains the shared, reviewable input catalogs used to resolve a
driver request. A catalog is a small source-side index; it is not a migration
workspace, a build output, or a substitute for frozen evidence.

Each catalog records the catalog identity and version, source platform, candidate
IDs, aliases, source entry hints, device/bus scope, device IDs, QEMU models, and
evidence hints. The catalog helps stage 3 identify the exact candidate. The
acquisition stage still verifies the candidate against the pinned original Linux,
Asterinas, and QEMU repositories and stores the resulting manifest under that
experiment's `.dpf/` directory.

The repository currently provides:

| File | Intended candidate |
| --- | --- |
| `drivers/linux-ne2000.catalog.json` | Linux NE2000 candidates, including PCI, ISA, and PCMCIA aliases for the scope-confirmation fixture |
| `drivers/linux-e1000.catalog.json` | Linux `e1000` PCI / QEMU `e1000` (`0x8086:0x100e`) |
| `drivers/linux-pvpanic-pci.catalog.json` | Linux `pvpanic-pci` / QEMU `pvpanic` (`0x1b36:0x0011`) |

Pass a catalog explicitly when starting a run:

```sh
./scripts/run-experiment.sh \
  --workspace ./runs/e1000 \
  --driver-name e1000 \
  --catalog configs/drivers/linux-e1000.catalog.json
```

Catalog paths are read when the request is resolved. Once the request and
acquisition artifacts are frozen, changing this shared file does not rewrite
the old run. Keep the old run's `.dpf/` directory as the provenance record and
create a new workspace when changing a catalog. Do not copy generated
manifests, CAS artifacts, QEMU receipts, or worker output back into this
directory.
