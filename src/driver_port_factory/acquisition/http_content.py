from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.models import GeneratedArtifact, utc_now
from ..core.project import Project
from .contracts import AcquisitionArtifact, AcquisitionStage
from .facets import RetrievalOutcome
from .http_transport import DownloadedHttpContent, HttpTransport
from .material import EvidenceContentRef, HttpResponseRecord
from .retrieval_result import RetrievalFailure


@dataclass(frozen=True, slots=True)
class RecordedHttpContent:
    data: bytes
    response: HttpResponseRecord
    path: Path


class EvidenceHttpContentRecorder:
    def __init__(self, project: Project, transport: HttpTransport | None = None) -> None:
        self.project = project
        self.transport = transport or HttpTransport()

    def retrieve(
        self,
        source_url: str,
        *,
        expected_sha256: str | None,
        max_bytes: int,
    ) -> RecordedHttpContent:
        downloaded = self.transport.download(source_url, max_bytes=max_bytes)
        recorded = self._record(downloaded)
        if expected_sha256 is not None and recorded.response.sha256 != expected_sha256:
            raise RetrievalFailure(
                RetrievalOutcome.CONFLICT,
                "download hash mismatch: "
                f"expected {expected_sha256}, got {recorded.response.sha256}",
                (recorded.response.content_ref,),
            )
        return recorded

    def _record(self, downloaded: DownloadedHttpContent) -> RecordedHttpContent:
        occurrence = self.project.record_artifact(
            AcquisitionStage.EVIDENCE_CLOSURE,
            GeneratedArtifact(
                AcquisitionArtifact.EVIDENCE_HTTP_CONTENT,
                downloaded.data,
                downloaded.response.resolved_url,
            ),
        )
        content_ref = EvidenceContentRef.from_artifact(occurrence)
        response = HttpResponseRecord(
            downloaded.response.requested_url,
            downloaded.response.resolved_url,
            downloaded.response.sha256,
            downloaded.response.size_bytes,
            downloaded.response.media_type,
            utc_now(),
            content_ref,
        )
        return RecordedHttpContent(
            downloaded.data,
            response,
            self.project.artifacts.path_for_digest(occurrence.digest),
        )
