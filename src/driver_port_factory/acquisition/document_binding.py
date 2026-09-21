"""Freeze a public document from semantic URL choices, without model-written hashes."""
from __future__ import annotations

from .authority import CorroboratedAuthority, CorroborationLocator, OriginalPublisherAuthority
from .authority_verification import PublisherIdentity, verify_publisher
from .facets import MaterialRedistribution
from .http_content import EvidenceHttpContentRecorder
from .locators import ExternalUrlLocator, MaterialPolicy
from .parsing import exact_object, http_url, nonempty
from .retrieval_result import RetrievalFailure
from ..core.models import WorkflowError
from ..core.project import Project


class ExternalDocumentBinder:
    def __init__(self, project: Project) -> None:
        self.content = EvidenceHttpContentRecorder(project)

    def bind(self, value: object) -> ExternalUrlLocator:
        candidate = exact_object(value, required={"url"},
                                 optional={"corroboration_urls", "publisher_url", "basis"},
                                 label="external document")
        url = http_url(candidate["url"], "document URL")
        limit = 16 * 1024 * 1024
        if "publisher_url" in candidate:
            if "corroboration_urls" in candidate:
                raise WorkflowError("choose original publisher or corroborated mirror, not both")
            authority = OriginalPublisherAuthority(http_url(candidate["publisher_url"], "publisher URL"),
                                                   nonempty(candidate.get("basis"), "publisher evidence"))
            primary = self.content.retrieve(url, expected_sha256=None, max_bytes=limit)
            verify_publisher(authority, primary.response.resolved_url)
            return ExternalUrlLocator(url, "sha256:" + primary.response.sha256,
                primary.response.sha256, limit, authority,
                MaterialPolicy("review-required", MaterialRedistribution.UNKNOWN, True))
        others = candidate.get("corroboration_urls")
        if not isinstance(others, list) or not others:
            raise WorkflowError("external document needs an independently published copy of the same original")
        urls = tuple(http_url(item, "corroboration URL") for item in others)
        try:
            primary = self.content.retrieve(url, expected_sha256=None, max_bytes=limit)
            copies = [self.content.retrieve(other, expected_sha256=primary.response.sha256,
                                           max_bytes=limit) for other in urls]
        except RetrievalFailure as error:
            raise WorkflowError(f"external document retrieval failed: {error.detail}; correct URLs or declare a gap") from error
        publisher = PublisherIdentity.from_url(primary.response.resolved_url)
        if not any(PublisherIdentity.from_url(copy.response.resolved_url) != publisher for copy in copies):
            raise WorkflowError("document copies resolve to the same publisher; do not claim independent corroboration")
        authority = CorroboratedAuthority(tuple(
            CorroborationLocator(copy.response.requested_url, copy.response.sha256, limit)
            for copy in copies
        ))
        return ExternalUrlLocator(
            url, "sha256:" + primary.response.sha256, primary.response.sha256, limit,
            authority, MaterialPolicy("review-required", MaterialRedistribution.UNKNOWN, True),
        )
