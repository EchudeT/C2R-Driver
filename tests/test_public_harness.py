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
from driver_port_factory.migration.public_repair import validate_public_repair_bundle
from driver_port_factory.environment.execution import ExperimentExecutor
from driver_port_factory.environment.inventory import EnvironmentInspector
from tests.test_environment import acquired_project


def test_final_review_is_required_and_bound_to_its_content(tmp_path: Path):
    report = {"schema_version": 2, "outcome": "NOT_APPLICABLE"}
    context = SimpleNamespace(one_current=lambda _: (None, json.dumps(report).encode()))
    with pytest.raises(WorkflowError, match="final evidence review"):
        validate_public_repair_bundle(context)
    text = "Reviewed actual code, oracle, and fresh logs.\nDPF_REVIEW: PASS\n"
    report["review"] = {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    validate_public_repair_bundle(context)
    report["review"]["text"] = "Changed conclusion.\nDPF_REVIEW: PASS\n"
    with pytest.raises(WorkflowError, match="final evidence review"):
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
    script.write_text('echo "QMP_READY PASS"\n')
    report = project.root / "report.md"
    report.write_text("Claimed ready; verify execution independently.\n")
    result = ExperimentExecutor().run_codex_harness(
        project, script_path=script, work_report_path=report,
    )
    assert result.readiness.value == "FAIL"
    attempt = json.loads(Path(result.attempt_path).read_text())
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
    script.write_text(f'"{qemu}" "$DPF_RUNTIME_ARTIFACT"\n')

    first = run_public_harness(
        attempt_dir=tmp_path / "first", script_path=script,
        worktree=worktree, runtime_path=runtime,
    )
    assert first.qemu_execs and first.runtime_bound
    assert not first.passed and not first.logs

    script.write_text(
        f'"{qemu}" "$DPF_RUNTIME_ARTIFACT"\n'
        'printf "new run\\n" > .dpf-output/qemu-runs/serial.log\n'
    )
    second = run_public_harness(
        attempt_dir=tmp_path / "second", script_path=script,
        worktree=worktree, runtime_path=runtime,
    )
    assert second.passed
    saved_trace = second.trace_path.read_bytes()

    script.write_text(
        f'"{tmp_path / "qemu-system-missing"}" "$DPF_RUNTIME_ARTIFACT" || true\n'
        'printf "another run\\n" > .dpf-output/qemu-runs/serial.log\n'
    )
    failed = run_public_harness(
        attempt_dir=tmp_path / "failed", script_path=script,
        worktree=worktree, runtime_path=runtime,
    )
    assert not failed.passed and not failed.qemu_execs
    assert second.trace_path.read_bytes() == saved_trace
