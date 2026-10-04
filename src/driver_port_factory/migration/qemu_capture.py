#!/usr/bin/env python3
"""Adapted from sibling driver-port-lab/adapters/qemu_capture.py (2026-10-02).

QEMU executable wrapper for one diagnostic run; usable inside the measured image.

The SDK supplies the original guest arguments. No source, test or acceptance logic
is injected. GDB stops/resumes guest CPUs, so this run is never acceptance evidence.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def capture(config, arguments):
    output = Path(config["output"])
    output.mkdir(parents=True, exist_ok=True)
    record = {"status": "unavailable", "diagnostic_only": True, "samples": []}
    process = None
    try:
        qemu, gdb = shutil.which(config["qemu"]), shutil.which("gdb")
        if not qemu or not gdb:
            raise RuntimeError("Configured QEMU or gdb is unavailable; no alternative selected")
        if any(a in ("-gdb", "-s", "-S", "-daemonize") for a in arguments):
            raise ValueError(
                "Diagnostic capture requires an unpaused foreground guest without another GDB server"
            )
        elf = Path(config["elf"])
        if not elf.is_file():
            raise ValueError("The SDK's unstripped guest ELF is absent")
        record.update(
            qemu=qemu,
            elf=str(elf),
            elf_sha256=hashlib.sha256(elf.read_bytes()).hexdigest(),
            argv=[qemu, *arguments],
        )
        with tempfile.TemporaryDirectory(prefix="dpf-gdb-") as sockets:
            address = str(Path(sockets) / "guest.sock")
            process = subprocess.Popen(
                [qemu, *arguments, "-gdb", f"unix:{address},server=on,wait=off"]
            )
            try:
                process.wait(timeout=config["capture_after_seconds"])
            except subprocess.TimeoutExpired:
                pass
            else:
                record.update(
                    status="guest_exited_before_capture", guest_returncode=process.returncode
                )
                return
            # Two bounded samples expose whether CPUs remain at the same place.
            for number in (1, 2):
                commands = output / f"sample-{number}.gdb"
                commands.write_text(
                    "set pagination off\nset confirm off\nset print frame-arguments none\n"
                    "set debuginfod enabled off\nset auto-load safe-path /nonexistent\n"
                    "set remotetimeout 5\n"
                    + "file "
                    + json.dumps(str(elf))
                    + "\n"
                    + "target remote "
                    + address
                    + "\n"
                    + "info threads\nthread apply all bt 24\n"
                    "thread apply all info registers\nthread apply all x/8i $pc\ndetach\nquit\n"
                )
                log = output / f"sample-{number}.log"
                with log.open("wb") as stream:
                    try:
                        result = subprocess.run(
                            [gdb, "-nx", "-nh", "-batch", "-x", str(commands)],
                            stdout=stream,
                            stderr=subprocess.STDOUT,
                            timeout=15,
                            check=False,
                        )
                        record["samples"].append(
                            {"file": log.name, "returncode": result.returncode}
                        )
                    except subprocess.TimeoutExpired:
                        record["samples"].append(
                            {"file": log.name, "error": "GDB capture timed out"}
                        )
                        break
                if number == 1:
                    try:
                        process.wait(timeout=0.25)
                    except subprocess.TimeoutExpired:
                        pass
                    else:
                        break
            record["status"] = (
                "captured"
                if record["samples"] and all(s.get("returncode") == 0 for s in record["samples"])
                else "capture_incomplete"
            )
    except (OSError, ValueError, RuntimeError) as error:
        record["error"] = str(error)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        (output / "capture.json").write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    capture(json.loads(Path(__file__).with_name("config.json").read_text()), sys.argv[1:])
    # Even an early successful guest exit cannot masquerade as ordinary acceptance.
    sys.exit(124)
