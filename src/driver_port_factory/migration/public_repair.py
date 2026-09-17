from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..acquisition.repository import load_repository_acquisition
from ..core.models import GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..knowledge.index import file_sha256
from .contracts import ContractExecutionStatus, MigrationArtifact, MigrationStage
from .public_qemu import run_public_harness


class PublicRepairService:
    def prepare(self, project: Project) -> tuple[dict[str, Any], dict[str, Any]] | None:
        ref = project.artifact(
            MigrationStage.PUBLIC_QEMU_VALIDATION,
            MigrationArtifact.PUBLIC_QEMU_REPORT,
        )
        report = json.loads(project.artifacts.read(ref))
        if report.get("execution_status") == ContractExecutionStatus.PASS.value:
            return None
        return ref.to_dict(), report

    def finalize_codex_repair(
        self,
        project: Project,
        *,
        work_report_path: Path,
        failure: dict[str, Any],
    ) -> None:
        """Freeze Codex's edits and rerun its harness with mechanical QEMU proof."""

        acquisition = load_repository_acquisition(project)
        worktree = (project.root / acquisition.target_worktree.path).resolve()
        script = worktree / ".dpf-output" / "public-qemu.sh"
        runtime = worktree / ".dpf-output" / "runtime-artifact"
        if not script.is_file() or not runtime.is_file() or runtime.stat().st_size == 0:
            raise WorkflowError("public repair did not preserve its harness and runtime artifact")
        run_key = f"{file_sha256(script)[:12]}-{file_sha256(runtime)[:12]}"
        observed = run_public_harness(
            attempt_dir=project.control / "public-repair" / run_key,
            script_path=script,
            worktree=worktree,
            runtime_path=runtime,
        )
        passed = observed.passed
        changed = self._changed_implementation_files(worktree)
        report = {
            "schema_version": 2,
            "outcome": (
                ContractExecutionStatus.PASS.value
                if passed
                else ContractExecutionStatus.FAIL.value
            ),
            "failure_run_ids": [
                str(run.get("run_id"))
                for run in failure.get("runs", [])
                if run.get("execution_status") == ContractExecutionStatus.FAIL.value
            ],
            "run": {
                "execution_status": (
                    ContractExecutionStatus.PASS.value
                    if passed
                    else ContractExecutionStatus.FAIL.value
                ),
                "attribution": "TARGET_DRIVER_ON_QEMU" if passed else "INCONCLUSIVE",
                "command": asdict(observed.command),
                "script_sha256": file_sha256(script),
                "exec_trace": {
                    "path": str(observed.trace_path.relative_to(project.root)),
                    "sha256": file_sha256(observed.trace_path),
                    "executed_programs": list(observed.executed_programs),
                    "qemu_execs": list(observed.qemu_execs),
                    "runtime_bound": observed.runtime_bound,
                },
                "logs": list(observed.logs),
            },
            "runtime_artifact": {
                "path": str(runtime.relative_to(project.root)),
                "sha256": file_sha256(runtime),
                "size": runtime.stat().st_size,
            },
            "implementation_files": changed,
            "work_report": {
                "path": str(work_report_path.relative_to(project.root)),
                "sha256": file_sha256(work_report_path),
            },
            "recorded_at": utc_now(),
        }
        self._finalize(project, report)

    @staticmethod
    def _changed_implementation_files(worktree: Path) -> list[dict[str, Any]]:
        def git(*arguments: str) -> list[str]:
            result = subprocess.run(
                ["git", "-C", str(worktree), *arguments],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode:
                raise WorkflowError(f"Git inspection failed: {result.stderr.strip()}")
            return [line for line in result.stdout.splitlines() if line]

        changed = set(git("diff", "--name-only", "HEAD"))
        changed.update(git("ls-files", "--others", "--exclude-standard"))
        return [
            {"path": relative, "sha256": file_sha256(worktree / relative)}
            for relative in sorted(changed)
            if not relative.startswith(".dpf-output/") and (worktree / relative).is_file()
        ]

    def finalize_not_applicable(self, project: Project) -> None:
        if project.stage(MigrationStage.PUBLIC_REPAIR).status is StageStatus.READY:
            project.start(MigrationStage.PUBLIC_REPAIR)
        self._finalize(
            project,
            {
                "schema_version": 2,
                "outcome": ContractExecutionStatus.NOT_APPLICABLE.value,
                "reason": "The public QEMU run passed without repair.",
                "recorded_at": utc_now(),
            },
        )

    @staticmethod
    def _finalize(project: Project, report: dict[str, Any]) -> None:
        project.finalize_stage(
            MigrationStage.PUBLIC_REPAIR,
            (
                GeneratedArtifact(
                    MigrationArtifact.PUBLIC_REPAIR_REPORT,
                    (
                        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                    ).encode(),
                    "generated:public-repair",
                ),
            ),
        )


def validate_public_repair_bundle(context: BundleValidationContext) -> None:
    report = json_object(
        context.one_current(MigrationArtifact.PUBLIC_REPAIR_REPORT)[1],
        MigrationArtifact.PUBLIC_REPAIR_REPORT.value,
    )
    if report.get("schema_version") != 2:
        raise WorkflowError("public repair report has an invalid version")
    outcome = ContractExecutionStatus(report.get("outcome"))
    if outcome is ContractExecutionStatus.PASS:
        run = report.get("run")
        trace = run.get("exec_trace") if isinstance(run, dict) else None
        if (
            not isinstance(trace, dict)
            or not trace.get("qemu_execs")
            or trace.get("runtime_bound") is not True
            or not run.get("logs")
            or run.get("attribution") != "TARGET_DRIVER_ON_QEMU"
        ):
            raise WorkflowError("a passing repair lacks observed QEMU/runtime/log evidence")
