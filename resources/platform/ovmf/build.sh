#!/bin/bash
# Explicit platform preparation, outside translation/model calls. No firmware fallback.
set -euo pipefail
src=${1:?usage: build.sh EDK2_SOURCE OUTPUT [upstream|patched]}
out=${2:?output directory required}
variant=${3:-patched}
expected=d46aa46c8361194521391aa581593e556c707c6e
actual=$(git -c safe.directory="$src" -C "$src" rev-parse HEAD)
[ "$actual" = "$expected" ] || { echo 'Wrong EDK2 revision' >&2; exit 1; }
case "$variant" in upstream|patched) ;; *) exit 2 ;; esac
git -c safe.directory="$src" -C "$src" diff --quiet || {
    echo 'Use a clean pinned checkout; refusing unrelated source changes' >&2; exit 1;
}
if [ "$variant" = patched ]; then
    git -c safe.directory="$src" -C "$src" apply --ignore-space-change \
        "$(cd "$(dirname "$0")" && pwd)/pvpanic-small-bar.patch"
fi
mkdir -p "$out"
cd "$src"
set --
set +eu
. ./edksetup.sh
set -eu
command -v build >/dev/null
make -C BaseTools -j8
build -a X64 -t GCC5 -b RELEASE -p OvmfPkg/OvmfPkgX64.dsc -n 8
cp Build/OvmfX64/RELEASE_GCC5/FV/OVMF.fd "$out/OVMF.fd"
git -c safe.directory="$src" rev-parse HEAD > "$out/revision.txt"
git -c safe.directory="$src" diff -- MdeModulePkg/Bus/Pci/PciBusDxe/PciEnumeratorSupport.c > "$out/applied.patch"
git -c safe.directory="$src" submodule status > "$out/submodules.txt"
sha256sum "$out/OVMF.fd" > "$out/firmware.sha256"
