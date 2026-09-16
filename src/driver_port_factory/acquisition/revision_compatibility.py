from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..core.models import WorkflowError
from .parsing import exact_object, nonempty, object_id, sha256
from .repository_role import RepositoryRole


class CompatibilityClaimKind(StrEnum):
    MAINTENANCE = "maintenance"
    CROSS_REPOSITORY = "cross-repository-compatibility"


class CitationVerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"


class CompatibilityAssessmentStatus(StrEnum):
    INFERRED = "INFERRED"


@dataclass(frozen=True, slots=True)
class ResolvedRevisionBinding:
    role: RepositoryRole
    requested_ref: str
    resolved_commit: str

    def to_dict(self) -> dict[str, str]:
        return {
            "role": self.role.value,
            "requested_ref": self.requested_ref,
            "resolved_commit": self.resolved_commit,
        }

    @classmethod
    def from_dict(cls, value: object) -> ResolvedRevisionBinding:
        candidate = exact_object(
            value,
            required={"role", "requested_ref", "resolved_commit"},
            label="resolved revision binding",
        )
        try:
            role = RepositoryRole(candidate["role"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("resolved revision binding has an invalid role") from error
        return cls(
            role,
            nonempty(candidate["requested_ref"], "resolved revision requested ref"),
            object_id(candidate["resolved_commit"], "resolved revision commit"),
        )


@dataclass(frozen=True, slots=True)
class CompatibilityEvidence:
    source_url: str
    resolved_url: str
    claim: str
    excerpt: str
    claim_kind: CompatibilityClaimKind
    bindings: tuple[ResolvedRevisionBinding, ...]
    citation_status: CitationVerificationStatus
    compatibility_status: CompatibilityAssessmentStatus
    sha256: str
    size_bytes: int
    retrieved_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source_url": self.source_url,
            "resolved_url": self.resolved_url,
            "claim": self.claim,
            "excerpt": self.excerpt,
            "claim_kind": self.claim_kind.value,
            "bindings": [binding.to_dict() for binding in self.bindings],
            "citation_status": self.citation_status.value,
            "compatibility_status": self.compatibility_status.value,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "retrieved_at": self.retrieved_at,
        }

    @classmethod
    def from_dict(cls, value: object) -> CompatibilityEvidence:
        candidate = exact_object(
            value,
            required={
                "source_url",
                "resolved_url",
                "claim",
                "excerpt",
                "claim_kind",
                "bindings",
                "citation_status",
                "compatibility_status",
                "sha256",
                "size_bytes",
                "retrieved_at",
            },
            label="compatibility evidence",
        )
        try:
            claim_kind = CompatibilityClaimKind(candidate["claim_kind"])
            citation_status = CitationVerificationStatus(candidate["citation_status"])
            compatibility_status = CompatibilityAssessmentStatus(candidate["compatibility_status"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("compatibility evidence has invalid typed fields") from error
        bindings = candidate["bindings"]
        if not isinstance(bindings, list) or not bindings:
            raise WorkflowError("compatibility evidence bindings must be non-empty")
        parsed = tuple(ResolvedRevisionBinding.from_dict(item) for item in bindings)
        if len({binding.role for binding in parsed}) != len(parsed):
            raise WorkflowError("compatibility evidence binding roles must be unique")
        size = candidate["size_bytes"]
        if not isinstance(size, int) or size <= 0:
            raise WorkflowError("compatibility evidence requires non-empty content")
        return cls(
            nonempty(candidate["source_url"], "compatibility source URL"),
            nonempty(candidate["resolved_url"], "compatibility resolved URL"),
            nonempty(candidate["claim"], "compatibility claim"),
            nonempty(candidate["excerpt"], "compatibility excerpt"),
            claim_kind,
            parsed,
            citation_status,
            compatibility_status,
            sha256(candidate["sha256"], "compatibility evidence SHA256"),
            size,
            nonempty(candidate["retrieved_at"], "compatibility retrieval timestamp"),
        )
