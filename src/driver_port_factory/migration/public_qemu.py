from __future__ import annotations

import json
import os
import shutil
import stat
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..acquisition.repository import load_repository_acquisition
from ..codex.contracts import CodexOutputError
from ..core.contracts import ArtifactKey
from ..core.container_trace import ContainerTrace
from ..core.execution import CommandResult, CommandRunner, script_command
from ..core.models import FileArtifact, GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..core.trace import successful_execs
from ..core.validation import BundleValidationContext, json_object
from ..environment.contracts import EnvironmentArtifact
from ..environment.evidence import workspace_path
from ..knowledge.contracts import KnowledgeArtifact
from ..knowledge.index import file_sha256
from ..acquisition.contracts import AcquisitionArtifact
from .contracts import (
    ContractEvidenceStatus,
    ContractExecutionStatus,
    MigrationArtifact,
    MigrationStage,
    PublicRunAttribution,
)
from .review_policy import require_self_review
from .implementation import validate_worktree_snapshot

PUBLIC_QEMU_INPUTS = (
    MigrationArtifact.HANDOFF,
    MigrationArtifact.CONTRACTS,
    MigrationArtifact.TEST_PORT_MATRIX,
    MigrationArtifact.COMPLIANCE_REPORT,
    MigrationArtifact.RUNTIME_ARTIFACT,
    MigrationArtifact.ARTIFACT_IDENTITY,
    EnvironmentArtifact.EXPERIMENT_ROUTE,
    KnowledgeArtifact.QUERY_CONTRACT,
    AcquisitionArtifact.MATERIALS_MANIFEST,
)


def evidence_files(root: Path):
    """Index observations, not extracted operating systems or intermediate images.

    Excluded material stays on disk; the index does not delete run evidence.
    Prune directories before walking to avoid thousands of rootfs stat/hash calls.
    """
    for directory, folders, names in os.walk(root, followlinks=False):
        folders[:] = sorted(name for name in folders if name not in {
            "rootfs", "iso-root", "node_modules", ".git", "cargo-home"
        } and not (Path(directory) / name).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() in {
                ".log", ".txt", ".json", ".jsonl", ".pcap", ".pcapng",
                ".bin", ".md", ".sha256", ".exit", ".out", ".err", ".csv"
            }:
                yield path


def runtime_in_qemu_arguments(line: str, runtime_path: Path) -> bool:
    """Match exact argv paths, including QEMU -drive file= options."""
    try:
        argv, _ = json.JSONDecoder().raw_decode(line[line.index("["):])
    except (ValueError, json.JSONDecodeError):
        return False
    if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
        return False
    if any(arg in {"-version", "--version", "-help", "--help", "help", "?"} for arg in argv[1:]):
        return False
    runtime = str(runtime_path)
    for index, argument in enumerate(argv):
        if argument == runtime and index and argv[index - 1] in {
            "-kernel", "-bios", "-pflash", "-cdrom", "-hda", "-hdb", "-hdc", "-hdd", "-fda", "-fdb",
        }:
            return True
        if index and argv[index - 1] == "-drive" and ",," not in argument:
            if f"file={runtime}" in argument.split(","):
                return True
    return False


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
        for path in evidence_files(log_root)
    }
    with ContainerTrace(worktree, attempt_dir / "container-processes.json") as containers:
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
                *script_command(script_path),
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
    successful = successful_execs(lines) + containers.executions(trace_path)
    # The immutable exec trace retains every call; the summary only needs program identities.
    executed = tuple(dict.fromkeys(path for path, _ in successful))
    qemu_lines = tuple(
        line
        for path, line in successful
        if Path(path).name.startswith("qemu-system-")
    )
    runtime_bound = any(runtime_in_qemu_arguments(line, runtime_path) for line in qemu_lines)
    logs = (
        tuple(
            {
                "path": str(path.relative_to(worktree)),
                "sha256": file_sha256(path),
                "size": path.stat().st_size,
            }
            for path in sorted(evidence_files(log_root))
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
        if not work_report_path.read_text(encoding="utf-8").rstrip().endswith("\nDPF_RUN: PUBLIC_QEMU"):
            raise CodexOutputError("Prepare the harness and end the report with DPF_RUN: PUBLIC_QEMU; do not claim an unexecuted PASS.")
        if not script_path.is_file():
            raise CodexOutputError("public QEMU work did not create .dpf-output/public-qemu.sh")
        acquisition = load_repository_acquisition(project)
        worktree = workspace_path(project, acquisition.target_worktree.path)
        implementation = project.load_json_artifact(
            MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE)
        validate_worktree_snapshot(project.root, implementation)
        runtime = project.artifact(
            MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.RUNTIME_ARTIFACT
        )
        runtime_path = project.artifacts.path_for_digest(runtime.digest).resolve()
        script_digest = file_sha256(script_path)
        inputs = {kind.value: self._input(project, kind).to_dict() for kind in PUBLIC_QEMU_INPUTS}
        helpers = self._helper_inputs(worktree)
        previous = self._latest_attempt(project)
        if previous is not None:
            ref, saved = previous
            run = saved["runs"][0]
            if (saved.get("schema_version") == 4
                    and saved.get("execution_status") == "PASS" and saved["inputs"] == inputs
                    and run["script"]["sha256"] == script_digest
                    and run.get("helper_inputs") == helpers
                    and run["request_report"]["sha256"] == file_sha256(work_report_path)):
                return {"status": "PASS", "attempt": str(project.artifacts.path_for_digest(ref.digest))}
        attempt_dir = project.control / "public-qemu" / uuid.uuid4().hex
        observed = run_public_harness(
            attempt_dir=attempt_dir,
            script_path=script_path,
            worktree=worktree,
            runtime_path=runtime_path,
        )
        harness_unchanged = (script_path.is_file() and file_sha256(script_path) == script_digest
                             and self._helper_inputs(worktree) == helpers)
        passed = observed.passed and harness_unchanged
        run = {
            "run_id": "public-qemu-script",
            "command": asdict(observed.command),
            "script": {"path": str(script_path), "sha256": script_digest},
            "runtime_artifact": {"path": str(runtime_path), "sha256": runtime.digest},
            "helper_inputs": helpers,
            "request_report": {
                "path": str(work_report_path.relative_to(project.root)),
                "sha256": file_sha256(work_report_path),
            },
            "exec_trace": {
                "path": str(observed.trace_path.relative_to(project.root)),
                "sha256": file_sha256(observed.trace_path),
                "executed_programs": list(observed.executed_programs),
                "qemu_execs": list(observed.qemu_execs),
                "runtime_bound": observed.runtime_bound,
                "container_evidence": str(observed.trace_path.parent / "container-processes.json"),
                "container_evidence_sha256": file_sha256(observed.trace_path.parent / "container-processes.json"),
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
            "schema_version": 4,
            "inputs": inputs,
            "runs": [run],
            "status": StageStatus.PASS.value if passed else StageStatus.FAIL.value,
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
        validate_worktree_snapshot(project.root, implementation)
        # Return observations to the worker regardless of the heuristic verdict.
        # Acceptance is a separate decision; do not consume repair rounds here.
        return {"status": run["execution_status"], "attempt": str(project.artifacts.path_for_digest(attempt_ref.digest))}

    @staticmethod
    def _latest_attempt(project: Project):
        refs = [ref for ref in project.current_artifact_refs(stage=MigrationStage.PUBLIC_QEMU_VALIDATION)
                if ref.kind == MigrationArtifact.PUBLIC_QEMU_ATTEMPT.value]
        if not refs:
            return None
        ref = max(refs, key=lambda item: item.ordinal)
        return ref, json.loads(project.artifacts.read(ref))

    @staticmethod
    def _helper_inputs(worktree: Path) -> dict[str, dict[str, Any]]:
        root = worktree / ".dpf-output/harness"
        if root.is_symlink():
            raise CodexOutputError("harness directory must not be a symlink")
        result = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise CodexOutputError("harness helpers must be regular files, not symlinks")
            if path.is_file():
                result[str(path.relative_to(worktree))] = {
                    "sha256": file_sha256(path),
                    "mode": stat.S_IMODE(path.stat().st_mode),
                }
        return result

    def accept_self_review(self, project: Project, *, work_report_path: Path) -> None:
        try:
            require_self_review(work_report_path.read_text(encoding="utf-8"))
        except CodexOutputError as error:
            project.note_check(str(error))
        previous = self._latest_attempt(project)
        if previous is None:
            raise CodexOutputError("No controller execution receipt. Request DPF_RUN: PUBLIC_QEMU before final self-check.")
        attempt_ref, report = previous
        acquisition = load_repository_acquisition(project)
        worktree = workspace_path(project, acquisition.target_worktree.path)
        implementation = project.load_json_artifact(
            MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE)
        validate_worktree_snapshot(project.root, implementation)
        inputs = {kind.value: self._input(project, kind).to_dict() for kind in PUBLIC_QEMU_INPUTS}
        script_path = worktree / ".dpf-output/public-qemu.sh"
        run = report["runs"][0]
        if (report.get("schema_version") != 4
                or report.get("execution_status") != "PASS" or report["inputs"] != inputs
                or not script_path.is_file() or file_sha256(script_path) != run["script"]["sha256"]
                or self._helper_inputs(worktree) != run["helper_inputs"]):
            project.note_check("Receipt is failed or harness inputs changed. Inspect the recorded "
                               "attempt and determine whether current functionality is verified; "
                               "accept supported work or repair the affected checks.")
        report["attempt_sha256"] = attempt_ref.digest
        report["self_review_sha256"] = file_sha256(work_report_path)
        project.finalize_stage(
            MigrationStage.PUBLIC_QEMU_VALIDATION,
            (
                FileArtifact(MigrationArtifact.PUBLIC_QEMU_WORK_REPORT, work_report_path),
                GeneratedArtifact(
                    MigrationArtifact.PUBLIC_QEMU_REPORT,
                    self._json(report),
                    f"generated:public-qemu-receipt:{attempt_ref.digest}",
                ),
            ),
        )

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
    attempts = [(ref, data) for ref, data in context.current_stage_artifacts
                if ref.kind == MigrationArtifact.PUBLIC_QEMU_ATTEMPT.value
                and ref.digest == report.get("attempt_sha256")]
    if len(attempts) != 1:
        raise WorkflowError("public QEMU report has no unique matching attempt")
    attempt_ref, attempt_data = attempts[0]
    if {key: value for key, value in report.items()
        if key not in {"attempt_sha256", "self_review_sha256"}} != json_object(
        attempt_data, "public QEMU attempt"
    ):
        raise WorkflowError("public QEMU report differs from captured execution attempt")
    expected_inputs = {
        kind.value: context.one_dependency(kind)[0].to_dict() for kind in PUBLIC_QEMU_INPUTS
    }
    if (
        report.get("schema_version") != 4
        or report.get("inputs") != expected_inputs
        or report.get("attempt_sha256") != attempt_ref.digest
        or report.get("status") != StageStatus.PASS.value
    ):
        raise WorkflowError("public QEMU report is detached from its run")
    runs = report.get("runs")
    if not isinstance(runs, list) or len(runs) != 1:
        raise WorkflowError("public QEMU report has no unique executed run")
    run = runs[0]
    worker_ref, worker_data = context.one_current(MigrationArtifact.PUBLIC_QEMU_WORK_REPORT)
    require_self_review(worker_data.decode())
    if (report.get("execution_status") != "PASS" or run.get("execution_status") != "PASS"
            or report.get("self_review_sha256") != worker_ref.digest):
        raise WorkflowError("public result must pass and bind the worker's self-check report")
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
