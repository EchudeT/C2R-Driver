"""Offline diagnostic routes and a local fake guest, never QEMU acceptance."""

import json
import os
from pathlib import Path

import pytest

from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration.debugging import _bundle, _record
from driver_port_factory.migration.qemu_capture import capture
from tests.test_translation_acceleration import active_project


def test_no_runtime_receipt_does_not_invent_a_debug_route(tmp_path):
    project, _ = active_project(tmp_path)
    with pytest.raises(WorkflowError, match="runtime check"):
        _record(project)


def test_bundle_requires_matching_worktree_action_and_unstripped_elf(tmp_path):
    directory = tmp_path / "target/osdk/test"
    directory.mkdir(parents=True)
    elf = directory / "kernel.elf"
    elf.write_bytes(b"synthetic only")
    config = directory / "bundle.toml"
    config.write_text(
        'action="Test"\n[aster_bin]\npath="kernel.elf"\nstripped=false\n'
        '[config]\nwork_dir="/target"\n[config.test.qemu]\npath="qemu-system-x86_64"\n'
    )
    observation = {"mounts": [{"Source": str(tmp_path), "Destination": "/target"}]}
    assert _bundle(tmp_path, "test", observation) == (
        elf,
        "/target/target/osdk/test/kernel.elf",
        "qemu-system-x86_64",
    )
    config.write_text(config.read_text().replace("stripped=false", "stripped=true"))
    with pytest.raises(WorkflowError, match="unstripped"):
        _bundle(tmp_path, "test", observation)


def test_capture_preserves_samples_and_terminates_owned_guest(tmp_path, monkeypatch):
    # A real local process exercises ownership/cleanup; it is not a virtual machine.
    qemu = tmp_path / "qemu-fixture"
    qemu.write_text(
        "#!/usr/bin/env python3\nimport os,time\nfrom pathlib import Path\n"
        'Path(os.environ["TEST_GUEST_PID"]).write_text(str(os.getpid()))\ntime.sleep(20)\n'
    )
    qemu.chmod(0o755)
    gdb = tmp_path / "gdb"
    gdb.write_text('#!/bin/sh\necho "synthetic stack sample"\n')
    gdb.chmod(0o755)
    elf = tmp_path / "kernel.elf"
    elf.write_bytes(b"synthetic")
    pid = tmp_path / "pid"
    monkeypatch.setenv("TEST_GUEST_PID", str(pid))
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    output = tmp_path / "capture"
    capture(
        {"output": str(output), "qemu": str(qemu), "elf": str(elf), "capture_after_seconds": 1}, []
    )
    result = json.loads((output / "capture.json").read_text())
    assert result["status"] == "captured" and result["diagnostic_only"]
    assert len(result["samples"]) == 2
    assert not Path("/proc", pid.read_text()).exists()


def test_capture_rejects_preexisting_gdb_configuration(tmp_path):
    output = tmp_path / "capture"
    capture({"output": str(output), "qemu": "/bin/true", "elf": "/bin/true"}, ["-S"])
    result = json.loads((output / "capture.json").read_text())
    assert result["status"] == "unavailable"
    assert result["samples"] == []
