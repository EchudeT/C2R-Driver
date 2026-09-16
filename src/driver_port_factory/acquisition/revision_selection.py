from __future__ import annotations

import json

from ..core.models import ActorRole, GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..intake.catalog import normalize_name
from ..intake.contracts import IntakeArtifact, IntakeStage
from .contracts import AcquisitionArtifact, AcquisitionStage
from .job import ArtifactOccurrence
from .repository_role import RepositoryRole
from .revision_evidence import RevisionEvidenceRetriever
from .revision_manifest import RepositoryPlan, RevisionManifest
from .revision_proposal import load_revision_proposal
from .revision_resolution import RevisionResolver


class RevisionSelector:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)

    def select(
        self,
        project: Project,
        *,
        proposal: ArtifactOccurrence,
    ) -> RepositoryPlan:
        project.ensure_role(*self.ROLES)
        if project.stage(IntakeStage.ENVELOPE_FREEZE).status is not StageStatus.PASS:
            raise WorkflowError("revision selection requires a frozen migration envelope")
        if project.stage(AcquisitionStage.REVISION_SELECTION).status is not StageStatus.RUNNING:
            raise WorkflowError("revision_selection must be RUNNING before static selection")
        envelope = load_revision_proposal(project, proposal)
        envelope_ref = project.artifact(
            IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
        )
        if envelope.proposal.migration_envelope_sha256 != envelope_ref.digest:
            raise WorkflowError("revision proposal migration envelope drifted before selection")
        self._validate_platforms(project, envelope.proposal.repositories)
        resolver = RevisionResolver(project.root, project.control)
        repositories = tuple(
            resolver.resolve(
                role=candidate.role,
                platform=candidate.platform,
                url=candidate.url,
                requested_ref=candidate.requested_ref,
                selection_rule=candidate.selection_rule,
            )
            for candidate in envelope.proposal.repositories
        )
        retrieved_evidence = RevisionEvidenceRetriever().retrieve(
            envelope.proposal.compatibility_evidence,
            repositories,
        )
        plan = RepositoryPlan(
            1,
            repositories,
            tuple(item.record for item in retrieved_evidence),
            proposal,
            envelope.job_result,
            envelope_ref.digest,
            resolver.commands,
            utc_now(),
        )
        plan_document = plan.to_dict()
        revision_manifest = RevisionManifest(
            envelope_ref.digest,
            proposal,
            envelope.job_result,
            plan.compatibility_evidence,
            repositories,
        )
        project.finalize_stage(
            AcquisitionStage.REVISION_SELECTION,
            (
                self._artifact(
                    AcquisitionArtifact.REVISION_MANIFEST,
                    revision_manifest.to_dict(),
                ),
                self._artifact(AcquisitionArtifact.REPOSITORY_PLAN, plan_document),
                *(
                    GeneratedArtifact(
                        AcquisitionArtifact.REVISION_EVIDENCE_CONTENT,
                        item.data,
                        item.record.resolved_url,
                    )
                    for item in retrieved_evidence
                ),
            ),
        )
        return plan

    @staticmethod
    def _validate_platforms(project: Project, repositories) -> None:
        by_role = {candidate.role: candidate for candidate in repositories}
        expected = {
            RepositoryRole.SOURCE: project.config.source_platform,
            RepositoryRole.TARGET: project.config.target_platform,
            RepositoryRole.QEMU: "qemu",
        }
        for role, platform in expected.items():
            if normalize_name(by_role[role].platform) != normalize_name(platform):
                raise WorkflowError(
                    f"revision proposal {role.value} platform differs from frozen project scope"
                )

    @staticmethod
    def _artifact(kind: AcquisitionArtifact, value: dict[str, object]) -> GeneratedArtifact:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"
        return GeneratedArtifact(kind, data, f"generated:acquisition:{kind.value}")
