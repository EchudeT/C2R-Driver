"""Run unchanged upstream tests against one prebuilt ISO; no per-case kernel rebuild."""

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/dpf-platform")
from guest import Guest
from oracle import summaries

ROOT = Path("/root/asterinas")


def qemu_argv(artifact):
    raw = subprocess.check_output(["bash", "tools/qemu_args.sh", "normal"], cwd=ROOT, text=True)
    original = iter(shlex.split(raw))
    args = []
    for word in original:
        if word in {"-serial", "-monitor", "-chardev", "-display", "-cpu"}:
            next(original)
        elif word not in {"-nographic"}:
            args.append(word)
    return [
        "qemu-system-x86_64",
        *args,
        "-accel",
        "kvm",
        "-cpu",
        "host",
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        "stdio",
        "-cdrom",
        artifact,
    ]


def execute(guest, command):
    try:
        guest.assert_guest(command)
        return True
    except RuntimeError as error:
        if "GUEST_ASSERT_FAILED" not in str(error):
            raise
        return False


def main():
    case, artifact, output = sys.argv[1:]
    output = Path(output)
    result = {"status": "ERROR", "boot_passed": False, "case": case}
    os.chdir(ROOT)
    try:
        with Guest(qemu_argv(artifact), output, 150, ready_text="# ") as guest:
            guest.wait_serial("# ")
            guest.assert_guest("sh /test/boot_hello.sh")
            result["boot_passed"] = True
            if case == "boot":
                result["status"] = "PASS"
            elif case.startswith("iperf3/"):
                benchmark(guest, case, output, result)
            else:
                binary = "/test/" + str(
                    Path(case).relative_to("test/initramfs/src/regression").with_suffix("")
                )
                ok = execute(guest, binary)
                text = (output / "serial.log").read_text(errors="replace")
                details = summaries((ROOT / case).read_text(), text)
                result.update(details)
                result["status"] = "PASS" if ok and details["passed"] else "FAIL"
                device = {
                    "hwrng": "/dev/hwrng",
                    "block_device": "/dev/vda",
                    "file_io": "/dev/vda",
                    "short_rw": "/dev/vda",
                    "nvme": "/dev/nvme0n1",
                }
                result["device_present"] = execute(guest, "test -e " + device[Path(case).stem])
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
    output.mkdir(parents=True, exist_ok=True)
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    return 0 if result["status"] == "PASS" else 1


def benchmark(guest, case, output, result):
    result["device_present"] = execute(guest, "ip link show eth0")
    guest.send("/benchmark/common/bench_runner.sh " + shlex.quote(case) + " asterinas\n")
    guest.wait_serial("The VM is ready for the benchmark.")
    guest.observe(1)
    with (output / "host.log").open("w") as stream:
        host = subprocess.Popen(
            ["bash", str(ROOT / "test/initramfs/src/benchmark" / case / "host.sh")],
            stdout=stream,
            stderr=subprocess.STDOUT,
            env={**os.environ, "GUEST_SERVER_IP_ADDRESS": "10.0.2.15"},
        )
        deadline = time.monotonic() + 45
        try:
            while host.poll() is None and time.monotonic() < deadline:
                guest.pump()
            if host.poll() is None:
                host.terminate()
                result["host_timeout"] = True
            host.wait(timeout=5)
        finally:
            if host.poll() is None:
                host.kill()
                host.wait()
    rates = re.findall(
        r"(\d+(?:\.\d+)?)\s+Mbits/sec.*\b(receiver|sender)\b", (output / "host.log").read_text()
    )
    result.update(rates_mbits=rates, host_exit=host.returncode)
    roles = {role for value, role in rates if float(value) > 0}
    result["status"] = (
        "PASS" if host.returncode == 0 and roles == {"sender", "receiver"} else "FAIL"
    )


if __name__ == "__main__":
    sys.exit(main())
