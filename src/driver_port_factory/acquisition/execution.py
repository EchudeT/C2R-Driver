from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.ledger import canonical_json
from ..core.models import (
    ActorRole,
    FileArtifact,
    GeneratedArtifact,
    StageStatus,
    WorkflowError,
    utc_now,
)
from ..core.project import Project
from ..intake.contracts import IntakeArtifact, IntakeStage
from ..intake.resolver import SourceEntryVerifier
from .contracts import (
    AcquisitionArtifact,
    AcquisitionAttemptOutcome,
    AcquisitionEvidenceCategory,
    AcquisitionStage,
    CoverageDisposition,
)
from .git import GitAcquirer
from .materials import AcquisitionMaterialCollector, checkout_for
from .models import AcquisitionPlan, CheckoutRecord, RepositoryRole, RepositorySpec


@dataclass(frozen=True, slots=True)
class AcquisitionResult:
    status: StageStatus
    checkouts: tuple[CheckoutRecord, ...]
    target_worktree: str
    source_identity_consistent: bool


class EvidenceAcquirer:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)

    def acquire(self, project: Project) -> AcquisitionResult:
        try:
            return self._acquire(project)
        except Exception as error:
            self._preserve_failure(project, error)
            raise

    def _acquire(self, project: Project) -> AcquisitionResult:
        project.ensure_role(*self.ROLES)
        if project.stage(AcquisitionStage.EVIDENCE_ACQUISITION).status is not StageStatus.READY:
            raise WorkflowError("evidence_acquisition is not READY")
        plan = AcquisitionPlan.from_dict(
            project.load_json_artifact(
                AcquisitionStage.REVISION_SELECTION, AcquisitionArtifact.ACQUISITION_PLAN
            )
        )
        envelope_ref = project.artifact(
            IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
        )
        if envelope_ref.digest != plan.migration_envelope_digest:
            raise WorkflowError("migration envelope changed after acquisition planning")
        project.start(AcquisitionStage.EVIDENCE_ACQUISITION)
        git = GitAcquirer(project.root, project.control)
        checkout_names = {
            RepositoryRole.SOURCE: "source-baseline",
            RepositoryRole.TARGET: "target-baseline",
            RepositoryRole.QEMU: "qemu-baseline",
        }
        checkouts = tuple(
            git.acquire(spec, checkout_names[spec.role]) for spec in plan.repositories
        )
        target = self._spec(plan, RepositoryRole.TARGET)
        target_worktree = git.create_target_worktree(target, project.config.project_id)
        source = checkout_for(checkouts, RepositoryRole.SOURCE)
        source_root = project.root / source.checkout_path
        envelope = project.load_json_artifact(
            IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
        )
        identity = SourceEntryVerifier().verify(envelope, source_root)
        materials = AcquisitionMaterialCollector().collect(project, checkouts, envelope)
        paths = self._write_manifests(project, checkouts, target_worktree, identity, materials, git)
        artifacts = self._artifacts(paths, identity)
        if identity.consistent:
            project.finalize_stage(AcquisitionStage.EVIDENCE_ACQUISITION, artifacts)
            outcome = StageStatus.PASS
        else:
            outcome = self._record_identity_conflict(project, identity, paths)
        return AcquisitionResult(
            outcome,
            checkouts,
            target_worktree,
            identity.consistent,
        )

    @staticmethod
    def _write_manifests(
        project: Project,
        checkouts: tuple[CheckoutRecord, ...],
        target_worktree: str,
        identity: Any,
        materials: list[dict[str, Any]],
        git: GitAcquirer,
    ) -> tuple[Path, Path]:
        manifest = {
            "schema_version": 1,
            "project_id": project.config.project_id,
            "acquired_at": utc_now(),
            "migration_envelope_sha256": project.artifact(
                IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
            ).digest,
            "checkouts": [record.to_dict() for record in checkouts],
            "target_worktree": target_worktree,
            "source_identity_verification": asdict(identity),
            "commands": git.serialized_commands(),
            "coverage_inventory": {
                AcquisitionEvidenceCategory.SOURCE.value: CoverageDisposition.ACQUIRED.value,
                AcquisitionEvidenceCategory.TARGET.value: CoverageDisposition.ACQUIRED.value,
                AcquisitionEvidenceCategory.QEMU.value: CoverageDisposition.ACQUIRED.value,
                AcquisitionEvidenceCategory.HARDWARE.value: CoverageDisposition.GAP.value,
                AcquisitionEvidenceCategory.TESTS.value: CoverageDisposition.GAP.value,
                AcquisitionEvidenceCategory.TOOLING.value: CoverageDisposition.GAP.value,
            },
            "execution_policy": "downloaded repository content was not executed",
        }
        acquisition_path = project.control / "manifests" / "acquisition.json"
        acquisition_path.parent.mkdir(parents=True, exist_ok=True)
        acquisition_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        materials_path = project.root / "knowledge" / "manifests" / "materials.jsonl"
        materials_path.parent.mkdir(parents=True, exist_ok=True)
        materials_path.write_text(
            "".join(canonical_json(material) + "\n" for material in materials),
            encoding="utf-8",
        )
        return acquisition_path, materials_path

    @staticmethod
    def _artifacts(
        paths: tuple[Path, Path], identity: Any
    ) -> tuple[FileArtifact | GeneratedArtifact, ...]:
        identity_data = (
            json.dumps(asdict(identity), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode()
        return (
            FileArtifact(AcquisitionArtifact.ACQUISITION_MANIFEST, paths[0]),
            FileArtifact(AcquisitionArtifact.MATERIALS_MANIFEST, paths[1]),
            GeneratedArtifact(
                AcquisitionArtifact.SOURCE_IDENTITY_VERIFICATION,
                identity_data,
                "generated:source-entry-verifier",
            ),
        )

    def _record_identity_conflict(
        self,
        project: Project,
        identity: Any,
        paths: tuple[Path, Path],
    ) -> StageStatus:
        message = "; ".join(identity.conflicts)
        project.record_artifact(
            AcquisitionStage.EVIDENCE_ACQUISITION,
            self._attempt(
                "SourceIdentityConflict",
                message,
                {
                    "source_identity_verification": asdict(identity),
                    "preserved_paths": [str(path) for path in paths],
                },
            ),
        )
        project.complete(AcquisitionStage.EVIDENCE_ACQUISITION, StageStatus.FAIL, message=message)
        return StageStatus.FAIL

    def _preserve_failure(self, project: Project, error: Exception) -> None:
        with suppress(WorkflowError, OSError):
            if project.stage(AcquisitionStage.EVIDENCE_ACQUISITION).status is StageStatus.RUNNING:
                project.record_artifact(
                    AcquisitionStage.EVIDENCE_ACQUISITION,
                    self._attempt(type(error).__name__, str(error)),
                )
                project.complete(
                    AcquisitionStage.EVIDENCE_ACQUISITION,
                    StageStatus.FAIL,
                    message=str(error),
                )

    @staticmethod
    def _attempt(
        failure_type: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> GeneratedArtifact:
        value = {
            "schema_version": 1,
            "status": AcquisitionAttemptOutcome.FAIL,
            "failure_type": failure_type,
            "message": message,
            "recorded_at": utc_now(),
            "partial_paths_preserved": True,
            **(extra or {}),
        }
        data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        return GeneratedArtifact(
            AcquisitionArtifact.ACQUISITION_ATTEMPT,
            data,
            "generated:acquisition:attempt",
        )

    @staticmethod
    def _spec(plan: AcquisitionPlan, role: RepositoryRole) -> RepositorySpec:
        matches = [spec for spec in plan.repositories if spec.role is role]
        if len(matches) != 1:
            raise WorkflowError(f"acquisition plan requires exactly one {role.value} repository")
        return matches[0]
