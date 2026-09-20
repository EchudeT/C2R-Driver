"""Execution-boundary fixtures only; these do not represent migrated-driver success."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from driver_port_factory.core.models import WorkflowError
from driver_port_factory.core.trace import successful_execs
from driver_port_factory.migration.public_qemu import run_public_harness
from driver_port_factory.migration.public_qemu import evidence_files
from driver_port_factory.migration.public_repair import validate_public_repair_bundle
from driver_port_factory.environment.execution import ExperimentExecutor
from driver_port_factory.environment.inventory import EnvironmentInspector
from tests.test_environment import acquired_project


def test_evidence_index_prunes_staging_without_deleting_it(tmp_path):
    root = tmp_path / "runs"
    for name in ("one/serial.log", "one/traffic.pcap", "one/response.bin", "result.txt",
                 "one/rootfs/etc/settings.json", "one/iso-root/serial.log",
                 "one/boot.iso", "one/initramfs.cpio.gz"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"evidence")
    (root / "link.log").symlink_to(root / "one/serial.log")
    assert {str(path.relative_to(root)) for path in evidence_files(root)} == {
        "one/serial.log", "one/traffic.pcap", "one/response.bin", "result.txt"
    }
    assert (root / "one/rootfs/etc/settings.json").is_file()


def test_failed_execution_cannot_enter_evidence_closure():
    report = {"schema_version": 3, "outcome": "PASS"}
    context = SimpleNamespace(
        one_current=lambda _: (None, json.dumps(report).encode()),
        one_dependency=lambda _: (None, b'{"execution_status":"FAIL"}'),
    )
    with pytest.raises(WorkflowError, match="failed public run"):
        validate_public_repair_bundle(context)


def test_interleaved_execs_preserve_success_and_reject_failure():
    lines = [
        '100 execve("/bin/qemu-system-riscv64", ["qemu", "image"], 0x12 <unfinished ...>',
        '101 execve("/bin/missing", ["missing"], 0x13) = -1 ENOENT (No such file)',
        '100 <... execve resumed>) = 0',
    ]
    results = successful_execs(lines)
    assert len(results) == 1
    assert results[0][0] == "/bin/qemu-system-riscv64"
    assert '"image"' in results[0][1]


@pytest.mark.skipif(shutil.which("strace") is None, reason="strace required")
def test_current_environment_harness_rejects_printed_success(tmp_path):
    project = acquired_project(tmp_path)
    EnvironmentInspector().inspect(project)
    script = project.root / "environment-smoke.sh"
    script.write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
                      'items=(QMP_READY PASS)\nprintf "%s\\n" "${items[*]}"\n')
    report = project.root / "report.md"
    report.write_text("Claimed ready; verify execution independently.\n")
    result = ExperimentExecutor().run_codex_harness(
        project, script_path=script, work_report_path=report,
    )
    assert result.readiness.value == "FAIL"
    attempt = json.loads(Path(result.attempt_path).read_text())
    assert attempt["command"]["exit_code"] == 0
    assert not attempt["exec_trace"]["qemu_programs"]


@pytest.mark.skipif(shutil.which("strace") is None, reason="strace required")
def test_harness_rejects_old_logs_and_failed_exec(tmp_path: Path):
    worktree = tmp_path / "target"
    log_root = worktree / ".dpf-output/qemu-runs"
    log_root.mkdir(parents=True)
    (log_root / "serial.log").write_text("old success\n")
    runtime = tmp_path / "runtime"
    runtime.write_text("synthetic payload\n")
    qemu = tmp_path / "qemu-system-fixture"
    qemu.symlink_to("/bin/true")
    script = worktree / "run.sh"
    script.write_text(f'"{qemu}" -kernel "$DPF_RUNTIME_ARTIFACT"\n')

    first = run_public_harness(
        attempt_dir=tmp_path / "first", script_path=script,
        worktree=worktree, runtime_path=runtime,
    )
    assert first.qemu_execs and first.runtime_bound
    assert not first.passed and not first.logs

    script.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nitems=(one two)\n[[ ${items[1]} == two ]]\n'
        f'"{qemu}" -kernel "$DPF_RUNTIME_ARTIFACT"\n'
        'printf "new run\\n" > .dpf-output/qemu-runs/serial.log\n'
    )
    second = run_public_harness(
        attempt_dir=tmp_path / "second", script_path=script,
        worktree=worktree, runtime_path=runtime,
    )
    assert second.passed
    saved_trace = second.trace_path.read_bytes()

    script.write_text(
        f'"{tmp_path / "qemu-system-missing"}" -kernel "$DPF_RUNTIME_ARTIFACT" || true\n'
        'printf "another run\\n" > .dpf-output/qemu-runs/serial.log\n'
    )
    failed = run_public_harness(
        attempt_dir=tmp_path / "failed", script_path=script,
        worktree=worktree, runtime_path=runtime,
    )
    assert not failed.passed and not failed.qemu_execs
    assert second.trace_path.read_bytes() == saved_trace
