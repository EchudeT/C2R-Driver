from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from pathlib import Path

from ..acquisition.repository import load_repository_acquisition
from ..codex.contracts import CodexOutputError
from ..core.execution import CommandRunner
from ..core.models import FileArtifact, GeneratedArtifact, StageStatus, WorkflowError
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..environment.contracts import EnvironmentArtifact, EnvironmentStage
from ..knowledge.index import file_sha256
from .contracts import MigrationArtifact, MigrationStage


def _json(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


class ArtifactPreparationService:
    """Capture real files and rerun a presence checker, without model-written manifests."""

    def capture_codex_artifact(self, project: Project, report_path: Path) -> dict:
        stage = MigrationStage.ARTIFACT_PREPARATION
        if project.stage(stage).status is not StageStatus.RUNNING:
            raise WorkflowError("artifact_preparation must be RUNNING")
        acquisition = load_repository_acquisition(project)
        worktree = (project.root / acquisition.target_worktree.path).resolve()
        runtime = worktree / ".dpf-output/runtime-artifact"
        checker = worktree / ".dpf-output/check-presence.sh"
        for path in (runtime, checker):
            if not path.is_file() or path.is_symlink() or worktree not in path.resolve().parents:
                raise CodexOutputError(f"artifact preparation must create {path.name}")
        bundle_ref = project.artifact(
            MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE
        )
        bundle = json.loads(project.artifacts.read(bundle_ref))
        for item in bundle["files"]:
            path = worktree / item["path"]
            if not path.is_file() or file_sha256(path) != item["sha256"]:
                raise CodexOutputError(
                    "implementation changed after review; return to implementation"
                )
        runtime_hash = file_sha256(runtime)
        checker_hash = file_sha256(checker)
        attempt_dir = project.control / "artifact-preparation" / str(uuid.uuid4())
        command = CommandRunner(attempt_dir).run(
            ["/bin/sh", str(checker)],
            cwd=worktree,
            environment={
                "DPF_RUNTIME_ARTIFACT": str(runtime),
                "DPF_TARGET_WORKTREE": str(worktree),
            },
            timeout_seconds=300,
        )
        passed = (
            command.launched
            and not command.timed_out
            and command.exit_code == 0
            and file_sha256(runtime) == runtime_hash
            and file_sha256(checker) == checker_hash
        )
        attempt = {
            "schema_version": 1,
            "status": "PASS" if passed else "FAIL",
            "presence_check": asdict(command),
            "checker_sha256": checker_hash,
            "runtime_sha256": runtime_hash,
            "implementation_sha256": bundle_ref.digest,
        }
        attempt_ref = project.record_artifact(
            stage,
            GeneratedArtifact(
                MigrationArtifact.ARTIFACT_PREPARATION_ATTEMPT, _json(attempt), str(attempt_dir)
            ),
        )
        if not passed:
            raise CodexOutputError(f"presence checker failed; inspect {command.stderr_path}")
        mode = project.load_json_artifact(
            EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD
        )
        identity = {
            "schema_version": 1,
            "artifact_mode": mode["artifact_mode"],
            "runtime_artifact": {
                "path": str(runtime.relative_to(project.root)),
                "sha256": runtime_hash,
                "size": runtime.stat().st_size,
            },
            "attempt_sha256": attempt_ref.digest,
            "driver_presence": {
                "implementation_sha256": bundle_ref.digest,
                "runtime_artifact_sha256": runtime_hash,
                "checker_sha256": checker_hash,
            },
            "work_report": {
                "path": str(report_path.relative_to(project.root)),
                "sha256": file_sha256(report_path),
            },
            "runtime_status": "NOT_RUN",
        }
        project.finalize_stage(
            stage,
            (
                FileArtifact(MigrationArtifact.RUNTIME_ARTIFACT, runtime),
                GeneratedArtifact(
                    MigrationArtifact.ARTIFACT_IDENTITY,
                    _json(identity),
                    "generated:artifact-identity",
                ),
            ),
        )
        return identity


def validate_artifact_bundle(context: BundleValidationContext) -> None:
    runtime_ref, runtime = context.one_current(MigrationArtifact.RUNTIME_ARTIFACT)
    identity = json_object(context.one_current(MigrationArtifact.ARTIFACT_IDENTITY)[1], "identity")
    matches = [
        (ref, data)
        for ref, data in context.current_stage_artifacts
        if ref.kind == MigrationArtifact.ARTIFACT_PREPARATION_ATTEMPT.value
        and ref.digest == identity.get("attempt_sha256")
    ]
    if not matches:
        raise WorkflowError("runtime artifact has no matching preparation attempt")
    attempt_ref, attempt_data = matches[-1]
    attempt = json_object(attempt_data, "artifact attempt")
    implementation_ref, _ = context.one_dependency(MigrationArtifact.IMPLEMENTATION_BUNDLE)
    presence = identity.get("driver_presence", {})
    command = attempt.get("presence_check", {})
    if (
        identity.get("schema_version") != 1
        or identity.get("attempt_sha256") != attempt_ref.digest
        or identity.get("runtime_artifact", {}).get("sha256") != runtime_ref.digest
        or hashlib.sha256(runtime).hexdigest() != runtime_ref.digest
        or attempt.get("runtime_sha256") != runtime_ref.digest
        or presence.get("runtime_artifact_sha256") != runtime_ref.digest
        or presence.get("implementation_sha256") != implementation_ref.digest
        or attempt.get("implementation_sha256") != implementation_ref.digest
        or presence.get("checker_sha256") != attempt.get("checker_sha256")
        or not command.get("launched")
        or command.get("timed_out")
        or command.get("exit_code") != 0
        or attempt.get("status") != "PASS"
    ):
        raise WorkflowError("runtime artifact is detached from its implementation/presence check")
