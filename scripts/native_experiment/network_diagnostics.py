"""Opt-in QEMU network evidence around an unchanged native runtime (stdlib only)."""

import collections
import json
import re
import runpy
import struct
import subprocess
import sys
from pathlib import Path


def options(argv, output, request):
    """Append observation options; retain the configured device and network backend."""
    output = Path(output)
    if "," in str(output):
        raise ValueError("Diagnostic output path cannot contain a comma")
    events = request.get("trace_events", [])
    if not isinstance(events, list) or any(
        not isinstance(e, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", e) for e in events
    ):
        raise ValueError("trace_events must be literal QEMU event names, not wildcards")
    netdevs = []
    for index, word in enumerate(argv[:-1]):
        if word == "-netdev":
            netdevs.extend(re.findall(r"(?:^|,)id=([^,]+)", argv[index + 1]))
    netdev = request.get("netdev")
    if netdev is None and len(netdevs) == 1:
        netdev = netdevs[0]
    if netdev not in netdevs:
        raise ValueError(f"Select an existing netdev from {netdevs}; no backend substitution")
    args = [
        *argv,
        "-object",
        f"filter-dump,id=dpf_diag_dump,netdev={netdev},file={output / 'packets.pcap'},maxlen=256",
    ]
    if events:
        inventory = subprocess.run(
            [argv[0], "-trace", "help"], capture_output=True, text=True, check=True, timeout=10
        )
        known = set((inventory.stdout + inventory.stderr).splitlines())
        unknown = set(events) - known
        if unknown:
            raise ValueError("Events unavailable in the fixed QEMU: " + ", ".join(sorted(unknown)))
        path = output / "trace-events.txt"
        path.write_text("\n".join(events) + "\n")
        args += ["-trace", f"events={path}"]
    return args


def packet(data, original_length):
    row = {"wire_length": original_length, "captured_length": len(data)}
    if len(data) < 14:
        return {**row, "protocol": "short-ethernet"}
    row.update(src_mac=data[6:12].hex(":"), dst_mac=data[:6].hex(":"))
    kind = int.from_bytes(data[12:14], "big")
    offset = 14
    if kind == 0x8100 and len(data) >= 18:
        kind, offset = int.from_bytes(data[16:18], "big"), 18
    row["protocol"] = f"ethernet-0x{kind:04x}"
    payload = data[offset:]
    if kind == 0x0806 and len(payload) >= 28 and payload[4:6] == b"\x06\x04":
        row.update(
            protocol="ARP",
            operation=int.from_bytes(payload[6:8], "big"),
            src_ip=".".join(map(str, payload[14:18])),
            dst_ip=".".join(map(str, payload[24:28])),
        )
    elif kind == 0x0800 and len(payload) >= 20:
        header = (payload[0] & 15) * 4
        row.update(
            protocol={1: "ICMP", 6: "TCP", 17: "UDP"}.get(payload[9], "IPv4"),
            src_ip=".".join(map(str, payload[12:16])),
            dst_ip=".".join(map(str, payload[16:20])),
        )
        if header >= 20 and row["protocol"] in {"TCP", "UDP"} and len(payload) >= header + 4:
            row.update(
                src_port=int.from_bytes(payload[header : header + 2], "big"),
                dst_port=int.from_bytes(payload[header + 2 : header + 4], "big"),
            )
            if row["protocol"] == "TCP" and len(payload) >= header + 14:
                row["tcp_flags"] = hex(payload[header + 13])
    return row


def summarize(path):
    """Stream a bounded PCAP; report observations without a root-cause or PASS verdict."""
    counts, samples = collections.Counter(), []
    packets = 0
    with Path(path).open("rb") as stream:
        header = stream.read(24)
        formats = {b"\xd4\xc3\xb2\xa1": "<", b"\xa1\xb2\xc3\xd4": ">"}
        endian = formats.get(header[:4])
        if len(header) != 24 or endian is None:
            raise ValueError("Unsupported or incomplete PCAP header")
        if struct.unpack(endian + "I", header[20:24])[0] != 1:
            raise ValueError("PCAP must contain Ethernet frames")
        while record := stream.read(16):
            if len(record) != 16:
                raise ValueError("Incomplete PCAP record")
            seconds, micros, length, original = struct.unpack(endian + "IIII", record)
            if length > 65536:
                raise ValueError("Unexpected PCAP record size")
            data = stream.read(length)
            if len(data) != length:
                raise ValueError("Incomplete PCAP packet")
            row = packet(data, original)
            packets += 1
            counts[row["protocol"]] += 1
            if len(samples) < 24:
                samples.append({"time": seconds + micros / 1e6, **row})
    return {"packets": packets, "protocol_counts": dict(counts), "first_packets": samples}


def execute(request, artifact, output):
    sys.path.insert(0, "/dpf-platform")
    import guest

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    base = guest.Guest
    limit = 16 * 1024 * 1024

    class ObservedGuest(base):
        def __init__(self, argv, directory, *args, **kwargs):
            super().__init__(options(argv, directory, request), directory, *args, **kwargs)

        def pump(self, *args, **kwargs):
            value = super().pump(*args, **kwargs)
            for filename in ("packets.pcap", "qemu-stderr.log", "serial.log"):
                path = self.output / filename
                if path.exists() and path.stat().st_size > limit:
                    raise RuntimeError("DIAGNOSTIC_LOG_LIMIT: " + filename)
            return value

    if request["runtime"] not in {"runtime.py", "public_runtime.py", "intel_runtime.py"}:
        raise ValueError("Diagnostic runtime must be an installed native runner")
    runtime = Path(__file__).with_name(request["runtime"])
    original_argv = sys.argv
    guest.Guest = ObservedGuest
    try:
        sys.argv = [str(runtime), request["case"], str(artifact), str(output)]
        try:
            runpy.run_path(str(runtime), run_name="__main__")
        except SystemExit:
            pass  # A failed workload still provides diagnostic evidence.
    finally:
        guest.Guest = base
        sys.argv = original_argv
    result = {"scope": "diagnostic-only; not acceptance", "output": str(output)}
    receipt = output / "result.json"
    if receipt.exists():
        result["workload"] = json.loads(receipt.read_text())
    pcap = output / "packets.pcap"
    if pcap.exists():
        try:
            result["capture"] = summarize(pcap)
        except ValueError as error:
            result["capture_error"] = str(error)
    trace = output / "qemu-stderr.log"
    if trace.exists():
        with trace.open("rb") as stream:
            stream.seek(max(0, trace.stat().st_size - 4096))
            result["trace_tail"] = stream.read().decode(errors="replace")
    (output / "diagnostics.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    request_path, artifact_path, output_path = sys.argv[1:]
    print(
        json.dumps(execute(json.loads(Path(request_path).read_text()), artifact_path, output_path))
    )
