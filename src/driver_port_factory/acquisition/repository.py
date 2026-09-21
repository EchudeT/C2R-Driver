from __future__ import annotations

import json
import hashlib
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
from .revision_manifest import RevisionManifest
from .revision_proposal import load_revision_proposal
from .job import ArtifactOccurrence
from ..intake.catalog import normalize_name
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

    def acquire(self, project: Project, *, proposal: ArtifactOccurrence | None = None) -> RepositoryAcquisition:
        project.ensure_role(*self.ROLES)
        stage = project.stage(AcquisitionStage.REPOSITORY_ACQUISITION)
        if stage.status is StageStatus.PASS:
            return load_repository_acquisition(project)
        if stage.status is StageStatus.READY:
            project.start(AcquisitionStage.REPOSITORY_ACQUISITION)
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(
                f"repository_acquisition must be READY or RUNNING, got {stage.status.value}"
            )
        try:
            if proposal is None:
                refs = [r for r in project.current_artifact_refs(stage=AcquisitionStage.REPOSITORY_ACQUISITION)
                        if r.kind == AcquisitionArtifact.REVISION_SELECTION_PROPOSAL.value]
                if not refs:
                    raise WorkflowError("repository acquisition requires a worker selection")
                ref = max(refs, key=lambda r: r.ordinal)
                proposal = ArtifactOccurrence(ref.digest, ref.ordinal)
            return self._acquire(project, proposal)
        except Exception as error:
            self._record_attempt(project, error)
            raise

    def _acquire(self, project: Project, proposal: ArtifactOccurrence) -> RepositoryAcquisition:
        selection = load_revision_proposal(project, proposal)
        envelope_ref = project.artifact(
            IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
        )
        if envelope_ref.digest != selection.proposal.migration_envelope_sha256:
            raise WorkflowError("migration envelope changed after revision selection")
        git = RepositoryGit(project.root, project.control)
        baselines = BaselineRepositoryAcquirer(project.root, project.control, git)
        expected = {RepositoryRole.SOURCE: project.config.source_platform,
                    RepositoryRole.TARGET: project.config.target_platform, RepositoryRole.QEMU: "qemu"}
        for candidate in selection.proposal.repositories:
            if normalize_name(candidate.platform) != normalize_name(expected[candidate.role]):
                raise WorkflowError("repository platform differs from frozen scope")
        checkouts = tuple(
            baselines.acquire(spec, self.CHECKOUT_NAMES[spec.role]) for spec in selection.proposal.repositories
        )
        repositories = tuple(RepositorySpec(c.role, c.platform, c.url, c.requested_ref,
                             checkout.resolved_commit, c.selection_rule)
                             for c, checkout in zip(selection.proposal.repositories, checkouts, strict=True))
        plan = RepositoryPlan(1, repositories, proposal, selection.job_result,
                              envelope_ref.digest, utc_now())
        plan_artifact = self._artifact(AcquisitionArtifact.REPOSITORY_PLAN, plan.to_dict())
        plan_digest = hashlib.sha256(plan_artifact.data).hexdigest()
        target_worktree = TargetWorktreeManager(project.root, project.control, git).create(
            self._spec(plan, RepositoryRole.TARGET), project.config.project_id
        )
        acquisition = RepositoryAcquisition(
            schema_version=1,
            project_id=project.config.project_id,
            acquired_at=utc_now(),
            migration_envelope_sha256=envelope_ref.digest,
            repository_plan_sha256=plan_digest,
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
                plan_artifact,
                self._artifact(AcquisitionArtifact.REVISION_MANIFEST,
                    RevisionManifest(envelope_ref.digest, proposal, selection.job_result, repositories).to_dict()),
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
