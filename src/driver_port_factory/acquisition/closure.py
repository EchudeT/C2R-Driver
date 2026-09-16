from __future__ import annotations

import json
from dataclasses import dataclass

from ..core.ledger import canonical_json
from ..core.models import GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..intake.contracts import IntakeArtifact, IntakeStage
from .closure_artifacts import (
    EvidenceClosureBundle,
    EvidenceCoverageInventory,
    EvidenceGapRegister,
    EvidenceMaterialsManifest,
    EvidenceRetrievalLedger,
)
from .contracts import AcquisitionArtifact, AcquisitionStage
from .evidence_collection import EvidenceCollector
from .frozen_checkout_validation import verify_git_checkout, verify_lock
from .job import ArtifactOccurrence, JobResultBinding, nonempty, sha256
from .parsing import exact_object, schema_version
from .proposal import EvidenceDiscoveryProposal, load_proposal_occurrence
from .repository import load_repository_acquisition
from .retrieval import EvidenceRetriever


@dataclass(frozen=True, slots=True)
class EvidenceClosurePlan:
    created_at: str
    migration_envelope_sha256: str
    repository_manifest_sha256: str
    proposal_occurrence: ArtifactOccurrence
    job_result: JobResultBinding
    proposal: EvidenceDiscoveryProposal
    schema_version = 1

    @classmethod
    def from_dict(cls, value: object) -> EvidenceClosurePlan:
        candidate = exact_object(
            value,
            required={
                "schema_version",
                "created_at",
                "migration_envelope_sha256",
                "repository_manifest_sha256",
                "proposal_occurrence",
                "job_result",
                "facets",
            },
            label="evidence closure plan",
        )
        schema_version(candidate, "evidence closure plan")
        occurrence = exact_object(
            candidate["proposal_occurrence"],
            required={"digest", "ordinal"},
            label="evidence closure proposal occurrence",
        )
        ordinal = occurrence["ordinal"]
        if not isinstance(ordinal, int) or ordinal < 0:
            raise WorkflowError("evidence closure plan has invalid proposal ordinal")
        proposal = EvidenceDiscoveryProposal.from_dict(
            {
                "schema_version": 1,
                "migration_envelope_sha256": candidate["migration_envelope_sha256"],
                "repository_manifest_sha256": candidate["repository_manifest_sha256"],
                "facets": candidate["facets"],
            }
        )
        return cls(
            nonempty(candidate["created_at"], "closure plan timestamp"),
            proposal.migration_envelope_sha256,
            proposal.repository_manifest_sha256,
            ArtifactOccurrence(sha256(occurrence["digest"], "proposal occurrence"), ordinal),
            JobResultBinding.from_dict(candidate["job_result"]),
            proposal,
        )

    def to_dict(self) -> dict[str, object]:
        proposal = self.proposal.to_dict()
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "migration_envelope_sha256": self.migration_envelope_sha256,
            "repository_manifest_sha256": self.repository_manifest_sha256,
            "proposal_occurrence": {
                "digest": self.proposal_occurrence.digest,
                "ordinal": self.proposal_occurrence.ordinal,
            },
            "job_result": self.job_result.to_dict(),
            "facets": proposal["facets"],
        }


@dataclass(frozen=True, slots=True)
class EvidenceClosureResult:
    controlled_materials: int
    explicit_gaps: int
    materials_manifest_digest: str


class EvidenceClosureFinalizer:
    def finalize(
        self,
        project: Project,
        *,
        proposal: ArtifactOccurrence,
    ) -> EvidenceClosureResult:
        if project.stage(AcquisitionStage.EVIDENCE_CLOSURE).status is not StageStatus.RUNNING:
            raise WorkflowError("evidence_closure must be RUNNING before finalization")
        envelope = load_proposal_occurrence(project, proposal)
        proposed = envelope.proposal
        envelope_ref = project.artifact(
            IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
        )
        repository_ref = project.artifact(
            AcquisitionStage.REPOSITORY_ACQUISITION,
            AcquisitionArtifact.REPOSITORY_MANIFEST,
        )
        if proposed.migration_envelope_sha256 != envelope_ref.digest:
            raise WorkflowError("migration envelope drifted after evidence discovery")
        if proposed.repository_manifest_sha256 != repository_ref.digest:
            raise WorkflowError("repository manifest drifted after evidence discovery")
        acquisition = load_repository_acquisition(project)
        self._verify_repositories(project, acquisition.checkouts)
        retriever = EvidenceRetriever(project, acquisition)
        envelope_document = project.load_json_artifact(
            IntakeStage.ENVELOPE_FREEZE,
            IntakeArtifact.MIGRATION_ENVELOPE,
        )
        collected = EvidenceCollector().collect(
            proposed,
            retriever,
            expected_source_entry=envelope_document["source_driver_entry_or_repository_hint"],
        )
        plan = EvidenceClosurePlan(
            utc_now(),
            envelope_ref.digest,
            repository_ref.digest,
            proposal,
            envelope.job_result,
            proposed,
        )
        coverage_inventory = EvidenceCoverageInventory(collected.coverage)
        gap_register = EvidenceGapRegister(collected.gaps)
        retrieval_ledger = EvidenceRetrievalLedger(collected.attempts)
        materials_manifest = EvidenceMaterialsManifest(collected.materials)
        EvidenceClosureBundle(
            plan,
            materials_manifest,
            coverage_inventory,
            gap_register,
            retrieval_ledger,
        )
        documents: tuple[tuple[AcquisitionArtifact, dict[str, object]], ...] = (
            (AcquisitionArtifact.EVIDENCE_CLOSURE_PLAN, plan.to_dict()),
            (
                AcquisitionArtifact.EVIDENCE_COVERAGE_INVENTORY,
                coverage_inventory.to_dict(),
            ),
            (
                AcquisitionArtifact.EVIDENCE_GAP_REGISTER,
                gap_register.to_dict(),
            ),
            (
                AcquisitionArtifact.EVIDENCE_RETRIEVAL_LEDGER,
                retrieval_ledger.to_dict(),
            ),
        )
        materials_data = "".join(
            canonical_json(record.to_dict()) + "\n" for record in collected.materials
        ).encode()
        artifacts = [self._json_artifact(kind, value) for kind, value in documents]
        artifacts.append(
            GeneratedArtifact(
                AcquisitionArtifact.MATERIALS_MANIFEST,
                materials_data,
                "generated:evidence-closure:controlled-materials",
            )
        )
        project.finalize_stage(AcquisitionStage.EVIDENCE_CLOSURE, tuple(artifacts))
        manifest_ref = project.artifact(
            AcquisitionStage.EVIDENCE_CLOSURE, AcquisitionArtifact.MATERIALS_MANIFEST
        )
        return EvidenceClosureResult(
            len(collected.materials), len(collected.gaps), manifest_ref.digest
        )

    @staticmethod
    def _verify_repositories(project: Project, checkouts) -> None:
        for checkout in checkouts:
            verify_lock(project.root, checkout)
            verify_git_checkout(project.root, checkout)

    @staticmethod
    def _json_artifact(kind: AcquisitionArtifact, value: dict[str, object]) -> GeneratedArtifact:
        data = (
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        )
        return GeneratedArtifact(kind, data, f"generated:evidence-closure:{kind.value}")
