# Explicit OVMF compatibility asset

This local quirk is **not an upstream firmware release or driver answer**. It handles
QEMU pvpanic-pci BAR0's exact anomalous sizing signature and leaves standard OVMF
resource allocation in charge. QEMU event behavior and the target kernel are unchanged.
See `docs/platform/OVMF_AND_EXECUTION_REFACTOR.zh-CN.md` and its audit directory for paired
upstream/patched firmware runs and limitations.

Reproduction (outside model translation, with network only for source/tools acquisition):

1. Clone EDK2 tag `edk2-stable202508`, require commit
   `d46aa46c8361194521391aa581593e556c707c6e`.
2. Initialize pinned submodules: `BaseTools/Source/C/BrotliCompress/brotli`,
   `CryptoPkg/Library/MbedTlsLib/mbedtls`, `CryptoPkg/Library/OpensslLib/openssl`,
   `MdeModulePkg/Library/BrotliCustomDecompressLib/brotli`,
   `MdeModulePkg/Universal/RegularExpressionDxe/oniguruma`,
   `MdePkg/Library/BaseFdtLib/libfdt`, `MdePkg/Library/MipiSysTLib/mipisyst`,
   `SecurityPkg/DeviceSecurity/SpdmLib/libspdm`.
3. Build `Dockerfile.builder` with the documented base image. Run `build.sh` in
   that container with mounted source/output directories and `upstream`, then
   `patched`. Both start from a clean checkout; patched applies only the included
   patch. Build is offline; the builder records source/submodule and binary hashes.
4. Test both firmware files with `scripts/verify-ovmf-pci.py` inside the base image,
   mounting a separately built, unmodified baseline ISO. Require `--expected
   unassigned` for upstream and `--expected assigned` for patched; test events
   1/2/4, two devices and no device. This is platform/model validation, not driver validation.
5. Create `manifest.json` with base image ID, EDK2 revision, submodule revisions,
   patch hash and firmware hash (schema/example in the audit directory), and use
   `Dockerfile.runtime` with output directory as build context. Keep the original
   official image tag intact. Freeze the resulting local image ID in new experiments.

No automatic selection, pull, firmware fallback or running-experiment update occurs.
One-time platform preparation costs must be reported separately as well as in total.
