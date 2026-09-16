from __future__ import annotations

from pathlib import Path

from ..core.models import utc_now
from .authority_verification import ExternalAuthorityVerifier
from .facets import EvidenceFacet, RetrievalOutcome
from .http_content import EvidenceHttpContentRecorder
from .locators import ExternalReferenceLocator, ExternalUrlLocator
from .material import ExternalUrlOrigin, MaterialRecord
from .material_content import MaterialContentPolicy
from .retrieval_result import RetrievalFailure, RetrievedMaterial


class ExternalMaterialRetriever:
    def __init__(
        self,
        project_root: Path,
        content: EvidenceHttpContentRecorder,
        authority: ExternalAuthorityVerifier,
    ) -> None:
        self.root = project_root.resolve()
        self.content = content
        self.authority = authority

    def retrieve_url(
        self,
        facet: EvidenceFacet,
        locator: ExternalUrlLocator,
        material_id: str,
    ) -> RetrievedMaterial:
        primary = self.content.retrieve(
            locator.source_url,
            expected_sha256=locator.expected_sha256,
            max_bytes=locator.max_bytes,
        )
        try:
            verification = self.authority.verify(locator.authority, locator, primary)
        except RetrievalFailure as error:
            if primary.response.content_ref in error.content_refs:
                raise
            raise error.with_content_refs(primary.response.content_ref) from error
        policy = locator.policy
        record = MaterialRecord(
            material_id,
            facet,
            str(primary.path.relative_to(self.root)),
            locator.source_url,
            locator.revision,
            utc_now(),
            policy.license_note,
            policy.redistribution,
            primary.response.sha256,
            primary.response.size_bytes,
            primary.response.media_type,
            policy.original,
            MaterialContentPolicy.indexable(primary.response.media_type, primary.path),
            ExternalUrlOrigin(
                locator.source_url,
                locator.revision,
                locator.authority,
                verification.authority,
                primary.response,
                verification.corroboration,
            ),
            policy.derived_from,
            policy.original_path,
            policy.page_map,
        )
        return RetrievedMaterial(
            record,
            f"downloaded and verified {primary.response.size_bytes} bytes with "
            f"{len(verification.corroboration)} corroboration retrievals",
            verification.content_refs,
        )

    def retrieve_reference(self, locator: ExternalReferenceLocator) -> RetrievedMaterial:
        retrieved = self.content.retrieve(
            locator.source_url,
            expected_sha256=None,
            max_bytes=locator.max_bytes,
        )
        raise RetrievalFailure(
            RetrievalOutcome.CONFLICT,
            "external reference exists and requires a hash-bound controlled download locator",
            (retrieved.response.content_ref,),
        )
