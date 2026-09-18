from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..acquisition.repository import load_repository_acquisition
from ..core.contracts import ArtifactKey
from ..core.execution import CommandResult, CommandRunner
from ..core.models import FileArtifact, GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..core.trace import successful_execs
from ..core.validation import BundleValidationContext, json_object
from ..environment.contracts import EnvironmentArtifact
from ..environment.evidence import workspace_path
from ..knowledge.contracts import KnowledgeArtifact
from ..knowledge.index import file_sha256
from ..source_analysis.contracts import SourceAnalysisArtifact
from .contracts import (
    ContractEvidenceStatus,
    ContractExecutionStatus,
    MigrationArtifact,
    MigrationStage,
    PublicRunAttribution,
)

PUBLIC_QEMU_INPUTS = (
    MigrationArtifact.HANDOFF,
    MigrationArtifact.CONTRACTS,
    MigrationArtifact.TEST_PORT_MATRIX,
    MigrationArtifact.TRANSLATION_COVERAGE,
    MigrationArtifact.RUNTIME_ARTIFACT,
    MigrationArtifact.ARTIFACT_IDENTITY,
    EnvironmentArtifact.EXPERIMENT_ROUTE,
    KnowledgeArtifact.QUERY_CONTRACT,
    SourceAnalysisArtifact.MATERIALS_MANIFEST,
)


@dataclass(frozen=True, slots=True)
class QemuHarnessResult:
    command: CommandResult
    trace_path: Path
    executed_programs: tuple[str, ...]
    qemu_execs: tuple[str, ...]
    runtime_bound: bool
    logs: tuple[dict[str, Any], ...]

    @property
    def passed(self) -> bool:
        return (
            self.command.launched
            and self.command.launch_error is None
            and not self.command.timed_out
            and self.command.exit_code == 0
            and bool(self.qemu_execs)
            and self.runtime_bound
            and bool(self.logs)
        )


def run_public_harness(
    *,
    attempt_dir: Path,
    script_path: Path,
    worktree: Path,
    runtime_path: Path,
) -> QemuHarnessResult:
    """Run a public harness and mechanically prove its QEMU/runtime/log boundary."""

    strace = shutil.which("strace")
    if strace is None:
        raise WorkflowError("strace is required to prove that the public harness executed QEMU")
    attempt_dir.mkdir(parents=True, exist_ok=True)
    trace_path = attempt_dir / "execve.log"
    log_root = worktree / ".dpf-output" / "qemu-runs"
    before = {
        path: (path.stat().st_mtime_ns, path.stat().st_size)
        for path in log_root.rglob("*") if path.is_file()
    }
    result = CommandRunner(attempt_dir / "command").run(
        [
            strace,
            "-f",
            "-qq",
            "-s",
            "65535",
            "-e",
            "trace=execve",
            "-o",
            str(trace_path),
            "/bin/sh",
            str(script_path),
        ],
        cwd=worktree,
        environment={
            "DPF_RUNTIME_ARTIFACT": str(runtime_path),
            "DPF_TARGET_WORKTREE": str(worktree),
        },
        timeout_seconds=3600,
    )
    lines = (
        trace_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if trace_path.is_file() else []
    )
    successful = successful_execs(lines)
    executed = tuple(path for path, _ in successful)
    qemu_lines = tuple(
        line
        for path, line in successful
        if Path(path).name.startswith("qemu-system-")
    )
    runtime_bound = any(f'"{runtime_path}"' in line for line in qemu_lines)
    logs = (
        tuple(
            {
                "path": str(path.relative_to(worktree)),
                "sha256": file_sha256(path),
                "size": path.stat().st_size,
            }
            for path in sorted(log_root.rglob("*"))
            if path.is_file() and path.stat().st_size > 0
            and before.get(path) != (path.stat().st_mtime_ns, path.stat().st_size)
        )
        if log_root.is_dir()
        else ()
    )
    return QemuHarnessResult(
        result,
        trace_path,
        executed,
        qemu_lines,
        runtime_bound,
        logs,
    )


class PublicQemuService:
    def run_script(
        self,
        project: Project,
        *,
        script_path: Path,
        work_report_path: Path,
    ) -> dict[str, Any]:
        """Execute the Codex-authored harness; never parse an AI run-plan schema."""

        if project.stage(MigrationStage.PUBLIC_QEMU_VALIDATION).status is not StageStatus.RUNNING:
            raise WorkflowError("public_qemu_validation must be RUNNING")
        if not script_path.is_file():
            raise WorkflowError("public QEMU work did not create .dpf-output/public-qemu.sh")
        acquisition = load_repository_acquisition(project)
        worktree = workspace_path(project, acquisition.target_worktree.path)
        runtime = project.artifact(
            MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.RUNTIME_ARTIFACT
        )
        runtime_path = project.artifacts.path_for_digest(runtime.digest).resolve()
        script_digest = file_sha256(script_path)
        attempt_dir = project.control / "public-qemu" / uuid.uuid4().hex
        observed = run_public_harness(
            attempt_dir=attempt_dir,
            script_path=script_path,
            worktree=worktree,
            runtime_path=runtime_path,
        )
        passed = observed.passed
        inputs = {kind.value: self._input(project, kind).to_dict() for kind in PUBLIC_QEMU_INPUTS}
        run = {
            "run_id": "public-qemu-script",
            "command": asdict(observed.command),
            "script": {"path": str(script_path), "sha256": script_digest},
            "runtime_artifact": {"path": str(runtime_path), "sha256": runtime.digest},
            "work_report": {
                "path": str(work_report_path.relative_to(project.root)),
                "sha256": file_sha256(work_report_path),
            },
            "exec_trace": {
                "path": str(observed.trace_path.relative_to(project.root)),
                "sha256": file_sha256(observed.trace_path),
                "executed_programs": list(observed.executed_programs),
                "qemu_execs": list(observed.qemu_execs),
                "runtime_bound": observed.runtime_bound,
            },
            "logs": list(observed.logs),
            "evidence_status": (
                ContractEvidenceStatus.VERIFIED.value
                if passed
                else ContractEvidenceStatus.UNKNOWN.value
            ),
            "execution_status": (
                ContractExecutionStatus.PASS.value
                if passed
                else ContractExecutionStatus.FAIL.value
            ),
            "attribution": (
                PublicRunAttribution.TARGET_DRIVER_ON_QEMU.value
                if passed
                else PublicRunAttribution.INCONCLUSIVE.value
            ),
        }
        report = {
            "schema_version": 2,
            "inputs": inputs,
            "runs": [run],
            "status": StageStatus.PASS.value,
            "execution_status": run["execution_status"],
            "integration_boundary": None if passed else "BLOCKED_FULL_INTEGRATION",
            "recorded_at": utc_now(),
        }
        attempt_path = attempt_dir / "attempt.json"
        attempt_path.write_bytes(self._json(report))
        attempt_ref = project.record_artifact(
            MigrationStage.PUBLIC_QEMU_VALIDATION,
            FileArtifact(MigrationArtifact.PUBLIC_QEMU_ATTEMPT, attempt_path),
        )
        report["attempt_sha256"] = attempt_ref.digest
        project.finalize_stage(
            MigrationStage.PUBLIC_QEMU_VALIDATION,
            (
                GeneratedArtifact(
                    MigrationArtifact.PUBLIC_QEMU_REPORT,
                    self._json(report),
                    f"generated:public-qemu-script:{script_digest}",
                ),
            ),
        )
        return {"status": run["execution_status"], "attempt": str(attempt_path)}

    @staticmethod
    def _input(project: Project, kind: ArtifactKey):
        dependencies = project.workflow.spec(MigrationStage.PUBLIC_QEMU_VALIDATION).dependencies
        matches = [
            ref
            for stage in dependencies
            for ref in project.current_artifact_refs(stage=stage)
            if ref.kind == kind.value
        ]
        if len(matches) != 1:
            raise WorkflowError(f"public QEMU validation requires one {kind.value} input")
        return matches[0]

    @staticmethod
    def _json(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def validate_public_qemu_bundle(context: BundleValidationContext) -> None:
    report = json_object(
        context.one_current(MigrationArtifact.PUBLIC_QEMU_REPORT)[1],
        MigrationArtifact.PUBLIC_QEMU_REPORT.value,
    )
    attempt_ref, _ = context.one_auxiliary(MigrationArtifact.PUBLIC_QEMU_ATTEMPT)
    expected_inputs = {
        kind.value: context.one_dependency(kind)[0].to_dict() for kind in PUBLIC_QEMU_INPUTS
    }
    if (
        report.get("schema_version") != 2
        or report.get("inputs") != expected_inputs
        or report.get("attempt_sha256") != attempt_ref.digest
        or report.get("status") != StageStatus.PASS.value
    ):
        raise WorkflowError("public QEMU report is detached from its run")
    runs = report.get("runs")
    if not isinstance(runs, list) or len(runs) != 1:
        raise WorkflowError("public QEMU report has no unique executed run")
    run = runs[0]
    if run.get("execution_status") == ContractExecutionStatus.PASS.value:
        trace = run.get("exec_trace")
        if (
            not isinstance(trace, dict)
            or not trace.get("qemu_execs")
            or trace.get("runtime_bound") is not True
            or not run.get("logs")
            or run.get("attribution") != PublicRunAttribution.TARGET_DRIVER_ON_QEMU.value
        ):
            raise WorkflowError("a passing public run lacks observed QEMU/runtime/log evidence")
