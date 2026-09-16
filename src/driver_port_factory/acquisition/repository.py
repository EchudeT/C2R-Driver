from __future__ import annotations

import json
from contextlib import suppress
from types import MappingProxyType

from ..core.models import ActorRole, GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..intake.contracts import IntakeArtifact, IntakeStage
from .baseline import BaselineRepositoryAcquirer
from .contracts import (
    AcquisitionArtifact,
    AcquisitionStage,
    RepositoryAcquisitionAttemptOutcome,
)
from .git_execution import RepositoryGit
from .repository_manifest import RepositoryAcquisition
from .repository_role import RepositoryRole
from .repository_spec import RepositorySpec
from .revision_manifest import RepositoryPlan
from .source_identity import SourceIdentityVerifier
from .target_worktree import TargetWorktreeManager


class RepositoryAcquirer:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)
    CHECKOUT_NAMES = MappingProxyType(
        {
            RepositoryRole.SOURCE: "source-baseline",
            RepositoryRole.TARGET: "target-baseline",
            RepositoryRole.QEMU: "qemu-baseline",
        }
    )

    def acquire(self, project: Project) -> RepositoryAcquisition:
        project.ensure_role(*self.ROLES)
        stage = project.stage(AcquisitionStage.REPOSITORY_ACQUISITION)
        if stage.status is StageStatus.READY:
            project.start(AcquisitionStage.REPOSITORY_ACQUISITION)
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(
                f"repository_acquisition must be READY or RUNNING, got {stage.status.value}"
            )
        try:
            return self._acquire(project)
        except Exception as error:
            self._record_attempt(project, error)
            raise

    def _acquire(self, project: Project) -> RepositoryAcquisition:
        plan = RepositoryPlan.from_dict(
            project.load_json_artifact(
                AcquisitionStage.REVISION_SELECTION,
                AcquisitionArtifact.REPOSITORY_PLAN,
            )
        )
        envelope_ref = project.artifact(
            IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
        )
        if envelope_ref.digest != plan.migration_envelope_digest:
            raise WorkflowError("migration envelope changed after revision selection")
        git = RepositoryGit(project.root, project.control)
        baselines = BaselineRepositoryAcquirer(project.root, project.control, git)
        checkouts = tuple(
            baselines.acquire(spec, self.CHECKOUT_NAMES[spec.role]) for spec in plan.repositories
        )
        target_worktree = TargetWorktreeManager(project.root, project.control, git).create(
            self._spec(plan, RepositoryRole.TARGET), project.config.project_id
        )
        plan_ref = project.artifact(
            AcquisitionStage.REVISION_SELECTION, AcquisitionArtifact.REPOSITORY_PLAN
        )
        acquisition = RepositoryAcquisition(
            schema_version=1,
            project_id=project.config.project_id,
            acquired_at=utc_now(),
            migration_envelope_sha256=envelope_ref.digest,
            repository_plan_sha256=plan_ref.digest,
            checkouts=checkouts,
            target_worktree=target_worktree,
            commands=git.records,
        )
        envelope = project.load_json_artifact(
            IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
        )
        identity = SourceIdentityVerifier().verify(
            project_root=project.root,
            migration_envelope_sha256=envelope_ref.digest,
            migration_envelope=envelope,
            source=acquisition.checkout(RepositoryRole.SOURCE),
        )
        project.finalize_stage(
            AcquisitionStage.REPOSITORY_ACQUISITION,
            (
                self._artifact(
                    AcquisitionArtifact.REPOSITORY_MANIFEST,
                    acquisition.to_dict(),
                ),
                self._artifact(
                    AcquisitionArtifact.SOURCE_IDENTITY_VERIFICATION,
                    identity.to_dict(),
                ),
            ),
        )
        return acquisition

    @staticmethod
    def _spec(plan: RepositoryPlan, role: RepositoryRole) -> RepositorySpec:
        matches = [spec for spec in plan.repositories if spec.role is role]
        if len(matches) != 1:
            raise WorkflowError(f"repository plan requires exactly one {role.value} repository")
        return matches[0]

    @staticmethod
    def _artifact(kind: AcquisitionArtifact, value: dict[str, object]) -> GeneratedArtifact:
        data = (
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        )
        return GeneratedArtifact(kind, data, f"generated:repository:{kind.value}")

    @staticmethod
    def _record_attempt(project: Project, error: Exception) -> None:
        with suppress(WorkflowError, OSError):
            if (
                project.stage(AcquisitionStage.REPOSITORY_ACQUISITION).status
                is not StageStatus.RUNNING
            ):
                return
            document = {
                "schema_version": 1,
                "status": RepositoryAcquisitionAttemptOutcome.FAIL.value,
                "failure_type": type(error).__name__,
                "message": str(error) or type(error).__name__,
                "recorded_at": utc_now(),
                "partial_paths_preserved": True,
            }
            project.record_artifact(
                AcquisitionStage.REPOSITORY_ACQUISITION,
                RepositoryAcquirer._artifact(
                    AcquisitionArtifact.REPOSITORY_ACQUISITION_ATTEMPT,
                    document,
                ),
            )


def load_repository_acquisition(project: Project) -> RepositoryAcquisition:
    return RepositoryAcquisition.from_dict(
        project.load_json_artifact(
            AcquisitionStage.REPOSITORY_ACQUISITION,
            AcquisitionArtifact.REPOSITORY_MANIFEST,
        )
    )
