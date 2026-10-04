#!/usr/bin/env python3
"""Inside the pinned container: firmware/BAR and unchanged QEMU device-model checks.

Not translated-driver acceptance. Qtest is used only by this diagnostic program,
with guest CPUs paused; it is not installed in the translation executor.
"""

import argparse
import json
import socket
import tempfile
from itertools import pairwise
from pathlib import Path

from driver_port_factory.platform.guest import Guest
from driver_port_factory.platform.profile import asterinas


def devices(buses):
    for bus in buses:
        for device in bus["devices"]:
            yield device
            if "pci_bridge" in device:
                yield from devices([device["pci_bridge"]["bus"]])


def validate_allocations(rows):
    regions = []
    for device in rows:
        for bar in device.get("regions", []):
            if bar["type"] != "memory" or bar["address"] < 0:
                continue
            start, size = bar["address"], bar["size"]
            assert size > 0 and start % size == 0, (device, bar)
            regions.append((start, start + size, device.get("slot"), bar["bar"]))
    regions.sort()
    for left, right in pairwise(regions):
        assert left[1] <= right[0], ("overlap", left, right)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--firmware", default="/root/ovmf/release/OVMF.fd")
    parser.add_argument("--expected", choices=("unassigned", "assigned"), required=True)
    parser.add_argument("--devices", type=int, default=1, choices=(0, 1, 2))
    parser.add_argument("--event", type=int, choices=(1, 2, 4))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    profile = asterinas("diagnostic", "diagnostic", "diagnostic", "kvm")
    qargs = profile["qemu_args"]
    qargs[qargs.index("-bios") + 1] = args.firmware
    with tempfile.TemporaryDirectory(prefix="dpf-pci-") as temporary:
        address = temporary + "/qtest.sock"
        argv = [
            profile["qemu"],
            *qargs,
            "-cdrom",
            args.artifact,
            "-qtest",
            f"unix:{address},server=on,wait=off",
            "-qtest-log",
            str(args.output / "qtest.log"),
            "-action",
            "panic=none,shutdown=poweroff",
        ]
        for n in range(args.devices):
            argv.extend(["-device", f"pvpanic-pci,id=pv{n},events=7"])
        with Guest(argv, args.output, 90) as guest:
            guest.until(lambda: b"# " in guest.serial, "unmodified-baseline-shell")
            guest.command("stop")
            pci = guest.command("query-pci")
            (args.output / "pci.json").write_text(json.dumps(pci, indent=2))
            rows = list(devices(pci))
            validate_allocations(rows)
            selected = [
                d for d in rows if d["id"]["vendor"] == 0x1B36 and d["id"]["device"] == 0x11
            ]
            assert len(selected) == args.devices
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(5)
                sock.connect(address)
                with sock.makefile("rwb", buffering=0) as stream:
                    result = check(guest, stream, selected, args)
            (args.output / "result.json").write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "scope": "firmware and QEMU model, not translated driver",
                        "expected": args.expected,
                        "devices": args.devices,
                        "observations": result,
                    },
                    indent=2,
                )
            )
    print(json.dumps({"status": "PASS", "output": str(args.output)}))


def check(guest, stream, selected, args):
    def qtest(command):
        stream.write(command.encode() + b"\n")
        response = stream.readline().decode().strip()
        assert response.startswith("OK"), (command, response)
        return response

    result = []
    for device in selected:
        assert device["bus"] == 0 and device["function"] == 0
        register = 0x80000000 | device["slot"] << 11 | 0x10
        qtest(f"outl 0xcf8 {register:#x}")
        raw = int(qtest("inl 0xcfc").split()[1], 16)
        base = raw & ~15
        assert bool(base) == (args.expected == "assigned"), (raw, device)
        bar = next(r for r in device["regions"] if r["bar"] == 0)
        assert bar["address"] == (base if base else -1)
        row = {"slot": device["slot"], "raw_bar0": raw, "qmp_bar": bar}
        if base:
            capability = int(qtest(f"readb {base:#x}").split()[1], 16)
            assert capability == 7
            row["capability"] = capability
        result.append(row)
    if args.event is not None:
        assert args.expected == "assigned" and selected
        base = result[0]["raw_bar0"] & ~15
        name = {1: "GUEST_PANICKED", 2: "GUEST_CRASHLOADED", 4: "GUEST_PVSHUTDOWN"}[args.event]
        qtest(f"writeb {base:#x} {args.event:#x}")
        guest.wait_event(name)
        result[0]["observed_event"] = name
    return result


if __name__ == "__main__":
    main()
