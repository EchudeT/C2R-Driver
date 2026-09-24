from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from pathlib import Path

from ..acquisition.repository import load_repository_acquisition
from ..codex.contracts import CodexOutputError
from ..core.execution import CommandRunner, script_command
from ..core.models import FileArtifact, GeneratedArtifact, StageStatus, WorkflowError
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..environment.contracts import EnvironmentArtifact, EnvironmentStage
from ..knowledge.index import file_sha256
from .contracts import MigrationArtifact, MigrationStage
from .implementation import validate_worktree_snapshot
from .target_framework import _validate_files


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
        framework = project.load_json_artifact(
            MigrationStage.TARGET_FRAMEWORK_ENABLEMENT,
            MigrationArtifact.TARGET_FRAMEWORK_BUNDLE,
        )
        # Check the separately sealed target framework before running any
        # packaging command.  A changed framework must not be hidden by a
        # successful presence check or a stale runtime identity.
        _validate_files(
            project.root,
            framework["target_worktree"],
            framework["files"],
        )
        validate_worktree_snapshot(project.root, bundle)
        runtime_hash = file_sha256(runtime)
        checker_hash = file_sha256(checker)
        variant_root = worktree / ".dpf-output/harness/variants"
        variants = {}
        if variant_root.is_symlink():
            raise CodexOutputError("runtime variants directory must not be a symlink")
        for path in sorted(variant_root.rglob("*")):
            if path.is_symlink():
                raise CodexOutputError("runtime variants must be regular files")
            if path.is_file():
                ref = project.record_artifact(stage, FileArtifact(MigrationArtifact.RUNTIME_VARIANT, path))
                variants[str(path.relative_to(worktree))] = ref.to_dict()
        attempt_dir = project.control / "artifact-preparation" / str(uuid.uuid4())
        command = CommandRunner(attempt_dir).run(
            script_command(checker),
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
            and all(file_sha256(worktree / path) == ref["digest"] for path, ref in variants.items())
        )
        attempt = {
            "schema_version": 1,
            "status": "PASS" if passed else "FAIL",
            "presence_check": asdict(command),
            "checker_sha256": checker_hash,
            "runtime_sha256": runtime_hash,
            "implementation_sha256": bundle_ref.digest,
            "variants": variants,
        }
        attempt_ref = project.record_artifact(
            stage,
            GeneratedArtifact(
                MigrationArtifact.ARTIFACT_PREPARATION_ATTEMPT, _json(attempt), str(attempt_dir)
            ),
        )
        if not passed:
            project.note_check(f"presence checker failed; inspect {command.stderr_path}")
        validate_worktree_snapshot(project.root, bundle)
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
            "variants": variants,
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
    target_framework = json.loads(
        context.one_dependency(MigrationArtifact.TARGET_FRAMEWORK_BUNDLE)[1]
    )
    # Recheck the enablement snapshot at the packaging boundary so a target
    # framework edit cannot silently drift between stages.
    _validate_files(
        context.project_root,
        target_framework["target_worktree"],
        target_framework["files"],
    )
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
    variants = identity.get("variants", {})
    current_variants = {ref.digest for ref, _ in context.current_stage_artifacts
                        if ref.kind == MigrationArtifact.RUNTIME_VARIANT.value}
    if variants != attempt.get("variants", {}) or any(
            ref["digest"] not in current_variants for ref in variants.values()):
        raise WorkflowError("runtime variants are detached from their preparation evidence")
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
