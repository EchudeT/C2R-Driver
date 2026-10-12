"""Offline packet fixtures and a local runner; no model or hardware driver claims."""

import importlib.util
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration.network_diagnostics import request

SCRIPT = Path(__file__).parents[1] / "scripts/native_experiment/network_diagnostics.py"
spec = importlib.util.spec_from_file_location("network_diagnostic_runner", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_capture_decodes_actual_arp_fields_and_tcp_flags(tmp_path):
    ethernet = bytes.fromhex("ffffffffffff5254001234560806")
    arp = bytes.fromhex("00010800060400015254001234560a00020f0000000000000a000202")
    tcp_frame = bytes.fromhex("5254001234560011223344550800")
    ip = bytes.fromhex("4500002800000000400600000a0002020a00020f")
    tcp = struct.pack("!HHII", 12345, 5201, 1, 0) + bytes.fromhex("5002000000000000")
    path = tmp_path / "packets.pcap"
    with path.open("wb") as stream:
        stream.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 256, 1))
        for payload in (ethernet + arp, tcp_frame + ip + tcp):
            stream.write(struct.pack("<IIII", 1, 500000, len(payload), len(payload)))
            stream.write(payload)
    value = runner.summarize(path)
    assert value["protocol_counts"] == {"ARP": 1, "TCP": 1}
    assert value["first_packets"][0]["src_ip"] == "10.0.2.15"
    assert value["first_packets"][0]["operation"] == 1
    assert value["first_packets"][1]["dst_port"] == 5201
    assert value["first_packets"][1]["tcp_flags"] == "0x2"


def test_observation_retains_route_and_checks_real_qemu_inventory(tmp_path, monkeypatch):
    argv = ["/fixed/qemu", "-netdev", "tap,id=net01,ifname=tap0", "-device", "e1000e"]
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout="nic_rx\nnic_tx\n", stderr=""),
    )
    result = runner.options(argv, tmp_path, {"trace_events": ["nic_rx"]})
    assert result[: len(argv)] == argv
    assert "netdev=net01" in result[len(argv) + 1]
    assert (tmp_path / "trace-events.txt").read_text() == "nic_rx\n"
    with pytest.raises(ValueError, match="unavailable"):
        runner.options(argv, tmp_path, {"trace_events": ["nonexistent_event"]})
    with pytest.raises(ValueError, match="existing netdev"):
        runner.options(argv, tmp_path, {"netdev": "other"})


def test_request_rejects_workload_changes_and_wildcard_traces():
    profile = {
        "native": {
            "task": {
                "network_required": True,
                "tests": ["nic-recovery"],
                "benchmarks": [],
                "runtime_script": "intel_runtime.py",
            }
        }
    }
    assert request(profile, {"case": "nic-recovery"})["runtime"] == "intel_runtime.py"
    for arguments in (
        {"case": "other"},
        {"case": "nic-recovery", "trace_events": ["nic_*"]},
        {"case": "nic-recovery", "device": "dummy"},
    ):
        with pytest.raises(WorkflowError):
            request(profile, arguments)


def test_failed_workload_still_exports_diagnostics_and_restores_transport(tmp_path, monkeypatch):
    class Guest:
        def __init__(self, argv, output, *args, **kwargs):
            self.output = output

        def pump(self):
            return None

    module = SimpleNamespace(Guest=Guest)
    monkeypatch.setitem(sys.modules, "guest", module)
    monkeypatch.setattr(runner, "options", lambda argv, output, request: argv)
    observed = []

    def workload(path, **kwargs):
        guest = module.Guest([], tmp_path)
        observed.append(guest)
        (tmp_path / "qemu-stderr.log").write_text("nic_rx observed\n")
        (tmp_path / "result.json").write_text(json.dumps({"status": "FAIL"}))
        raise SystemExit(1)

    monkeypatch.setattr(runner.runpy, "run_path", workload)
    value = runner.execute(
        {"runtime": "intel_runtime.py", "case": "nic-recovery"}, "x.iso", tmp_path
    )
    assert value["workload"]["status"] == "FAIL"
    assert "diagnostic-only" in value["scope"]
    assert module.Guest is Guest
    assert "nic_rx" in value["trace_tail"]
    (tmp_path / "packets.pcap").write_bytes(b"x" * (16 * 1024 * 1024 + 1))
    with pytest.raises(RuntimeError, match="DIAGNOSTIC_LOG_LIMIT"):
        observed[0].pump()
