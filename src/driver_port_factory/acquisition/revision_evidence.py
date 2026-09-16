from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..core.models import WorkflowError, utc_now
from .repository_role import RepositoryRole
from .repository_spec import RepositorySpec
from .revision_compatibility import (
    CitationVerificationStatus,
    CompatibilityAssessmentStatus,
    CompatibilityEvidence,
    ResolvedRevisionBinding,
)
from .revision_proposal import CompatibilityCitation

_ERROR_PAGE = re.compile(
    rb"<(?:title|h1)[^>]*>\s*(?:error|404|403|not found|access denied)", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class RetrievedCompatibilityEvidence:
    record: CompatibilityEvidence
    data: bytes


class RevisionEvidenceRetriever:
    def retrieve(
        self,
        citations: tuple[CompatibilityCitation, ...],
        repositories: tuple[RepositorySpec, ...],
    ) -> tuple[RetrievedCompatibilityEvidence, ...]:
        resolved = {repository.role: repository for repository in repositories}
        return tuple(self._retrieve(citation, resolved) for citation in citations)

    @staticmethod
    def _retrieve(
        citation: CompatibilityCitation,
        repositories: dict[RepositoryRole, RepositorySpec],
    ) -> RetrievedCompatibilityEvidence:
        request = urllib.request.Request(
            citation.source_url,
            headers={"User-Agent": "Driver-Port-Factory/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > citation.max_bytes:
                    raise WorkflowError("compatibility evidence exceeds its declared maximum")
                media_type = response.headers.get_content_type().lower()
                if not (
                    media_type.startswith("text/")
                    or media_type in {"application/json", "application/xml"}
                ):
                    raise WorkflowError(
                        "compatibility evidence must be an inspectable text document"
                    )
                data = response.read(citation.max_bytes + 1)
                if len(data) > citation.max_bytes:
                    raise WorkflowError("compatibility evidence exceeds its declared maximum")
                resolved_url = response.geturl()
        except urllib.error.HTTPError as error:
            raise WorkflowError(
                f"compatibility evidence retrieval failed with HTTP {error.code}"
            ) from error
        except urllib.error.URLError as error:
            raise WorkflowError(
                f"compatibility evidence retrieval failed: {error.reason}"
            ) from error
        if not data or _ERROR_PAGE.search(data[:16_384]):
            raise WorkflowError("compatibility evidence is empty or an error response")
        excerpt = citation.excerpt.encode("utf-8")
        if excerpt not in data:
            raise WorkflowError(
                "compatibility evidence citation excerpt is absent from retrieved content"
            )
        digest = hashlib.sha256(data).hexdigest()
        bindings = tuple(
            ResolvedRevisionBinding(
                binding.role,
                binding.requested_ref,
                repositories[binding.role].resolved_commit,
            )
            for binding in citation.bindings
        )
        record = CompatibilityEvidence(
            citation.source_url,
            resolved_url,
            citation.claim,
            citation.excerpt,
            citation.claim_kind,
            bindings,
            CitationVerificationStatus.VERIFIED,
            CompatibilityAssessmentStatus.INFERRED,
            digest,
            len(data),
            utc_now(),
        )
        return RetrievedCompatibilityEvidence(record, data)
