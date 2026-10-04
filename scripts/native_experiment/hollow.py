"""Remove a single implementation and its concrete integration, without algorithm stubs."""

import shutil
from pathlib import Path

VIRTIO = "kernel/core/comps/virtio/src/"


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError(f"Pinned integration text differs: {path}: {old[:60]}")
    path.write_text(text.replace(old, new))


def hollow(target, name, task):
    omitted = target / task["omit"]
    shutil.rmtree(omitted)
    if name == "nvme-pci":
        omitted.mkdir()
        (omitted / "lib.rs").write_text(
            "// SPDX-License-Identifier: MPL-2.0\n"
            "//! No NVMe device is registered in this baseline.\n#![no_std]\n"
        )
    else:
        module = Path(task["omit"]).name
        kind = task["device_type"]
        replace(target / VIRTIO / "device/mod.rs", f"pub mod {module};\n", "")
        lib = target / VIRTIO / "lib.rs"
        text = lib.read_text()
        text = text.replace(f"{module}::device::{kind}Device,", "")
        text = text.replace(f"    device::{module}::init();\n", "")
        text = text.replace(
            f"            VirtioDeviceType::{kind} => {kind}Device::init(device_transport),\n", ""
        )
        text = text.replace(
            (
                f"        VirtioDeviceType::{kind} => "
                f"{kind}Device::negotiate_features(device_specified_features),\n"
            ),
            "",
        )
        # Skip the absent device before touching status/features/queues.
        text = text.replace(
            "        let device_type = transport.device_type();",
            "        let device_type = transport.device_type();\n"
            f"        if device_type == VirtioDeviceType::{kind} {{ continue; }}",
        )
        lib.write_text(text)
    if name == "virtio-rng":
        # Keep the entire original VFS frontend on disk, but do not compile/register it.
        misc = target / "kernel/core/src/device/misc/mod.rs"
        replace(misc, "mod hwrng;", "// No hardware RNG backend is registered.")
        replace(misc, "    hwrng::init_in_first_kthread();", "")
    elif name in ("virtio-blk", "nvme-pci"):
        block = target / "kernel/core/src/device/registry/block.rs"
        text = block.read_text()
        if name == "virtio-blk":
            text = text.replace(
                "use aster_virtio::device::block::device::BlockDevice as VirtIoBlockDevice;\n", ""
            )
            start = text.index("        // Spawn threads for virtio block devices")
            end = text.index("        else if device.downcast_ref::<NvmeBlockDevice>()", start)
            text = text[:start] + text[end:].replace("        else if ", "        if ", 1)
        else:
            text = text.replace("use aster_nvme::NvmeBlockDevice;\n", "")
            start = text.index("        // Spawn threads for NVMe block devices")
            end = text.index("\n    }\n\n    // Partition scanning", start)
            text = text[:start] + text[end:]
        block.write_text(text)
    elif name == "virtio-net":
        net = target / "kernel/core/src/net/iface/init.rs"
        text = net.read_text().replace("aster_virtio::device::network::DEVICE_NAME", '"Virtio-Net"')
        start = text.index("fn new_virtio() -> Option<Arc<Iface>> {")
        end = text.index('\n#[cfg(target_arch = "x86_64")]\nfn new_ne2k()', start)
        text = (
            text[:start]
            + (
                "fn new_virtio() -> Option<Arc<Iface>> {\n"
                "    None // No VirtIO network device in the hollow baseline.\n}\n"
            )
            + text[end:]
        )
        net.write_text(text)
