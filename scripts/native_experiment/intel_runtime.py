"""Frozen Intel NIC tests: real native traffic and external QEMU queue observations."""

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/dpf-platform")
from guest import Guest

ROOT = Path("/root/asterinas")


def argv(spec, artifact, output, driver, trace_rss=False):
    profile = spec["profile"]
    command = [
        profile["qemu"],
        *profile["qemu_args"],
        "-cdrom",
        artifact,
    ]
    if trace_rss:
        command += [
            "-trace",
            "enable=e1000e_rx_written_to_guest",
            "-trace",
            "enable=igb_rx_metadata_rss",
        ]
    return command


def traffic(guest, case, output, parallel=1):
    guest.assert_guest("ip link show eth0")
    guest.send("/benchmark/common/bench_runner.sh " + shlex.quote(case) + " asterinas\n")
    guest.wait_serial("The VM is ready for the benchmark.")
    guest.observe(1)
    # Execute the upstream client command with its process-killing EXIT trap removed.
    # Own only this guest; never let an upstream broad pgrep kill another experiment.
    source = (ROOT / "test/initramfs/src/benchmark" / case / "host.sh").read_text()
    command = next(line for line in source.splitlines() if line.startswith("iperf3 "))
    if parallel > 1:
        command = command.replace("-b 0", "-b 2M").replace("-l 1460", "-l 512")
        command += f" -P {parallel}"
    with (output / "host.log").open("w") as log:
        host = subprocess.Popen(
            ["sh", "-c", command],
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "GUEST_SERVER_IP_ADDRESS": "10.0.2.15"},
        )
        try:
            import time

            deadline = time.monotonic() + 55
            while host.poll() is None and time.monotonic() < deadline:
                guest.pump()
            if host.poll() is None:
                raise RuntimeError("IPERF_HOST_TIMEOUT")
        finally:
            if host.poll() is None:
                host.terminate()
            host.wait(timeout=5)
    rates = re.findall(
        r"(\d+(?:\.\d+)?)\s+Mbits/sec.*\b(receiver|sender)\b",
        (output / "host.log").read_text(),
    )
    if host.returncode or {role for value, role in rates if float(value) > 0} != {
        "receiver",
        "sender",
    }:
        raise RuntimeError("NATIVE_TRAFFIC_FAILED")
    return rates


def execute(case, artifact, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)
    driver = json.loads((ROOT / ".native-task.json").read_text())["driver"]
    spec = json.loads(Path("/dpf-shared/public-tests/index.json").read_text())[driver + "/" + case]
    value = {
        "status": "FAIL",
        "boot_passed": False,
        "case": case,
        "artifact_sha256": hashlib.sha256(Path(artifact).read_bytes()).hexdigest(),
    }
    try:
        with Guest(
            argv(spec, artifact, output, driver, trace_rss=case == "igb-rss"),
            output,
            180,
            ready_text="# ",
        ) as guest:
            guest.wait_serial("# ")
            guest.assert_guest("sh /test/boot_hello.sh")
            value["boot_passed"] = True
            if case == f"{driver}-baseline-boot":
                pass
            elif case.startswith("iperf3/"):
                value["rates_mbits"] = traffic(guest, case, output)
            elif case == "igb-rss":
                guest.assert_guest("printf 'queues2\n' > /proc/igb_test")
                value["rates_mbits"] = traffic(guest, "iperf3/udp_virtio_bw_rx", output, 8)
            else:
                command = case.removeprefix(driver + "-")
                guest.assert_guest(f"printf '%s\n' '{command}' > /proc/{driver}_test")
                guest.wait_serial(f"DPF_INTEL_OK {driver} {command}")
                value["device_present"] = True
                if command in {"recovery", "diagnostics"}:
                    value["rates_mbits"] = traffic(guest, "iperf3/tcp_virtio_bw", output)
        if case == "igb-rss":
            text = (output / "qemu-stderr.log").read_text()
            queues = sorted(
                set(
                    map(
                        int,
                        re.findall(r"Received packet written to guest \(queue (\d+)\)", text),
                    )
                )
            )
            if queues != [0, 1] or "igb_rx_metadata_rss" not in text:
                raise RuntimeError(f"RSS_TWO_HARDWARE_QUEUES_REQUIRED: {queues}")
            value["hardware_rx_queues"] = queues
        value["status"] = "PASS"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        value["error"] = str(error)
    (output / "result.json").write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps(value), flush=True)
    return 0 if value["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(execute(*sys.argv[1:]))
