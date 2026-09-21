from __future__ import annotations

import hashlib
from pathlib import Path

from ..core.models import ArtifactRef, WorkflowError
from ..core.validation import BundleValidationContext
from .accounting import RetrievalAttempt
from .contracts import AcquisitionArtifact
from .facets import RetrievalOutcome
from .material import EvidenceContentRef, ExternalUrlOrigin, HttpResponseRecord, MaterialRecord


class EvidenceContentOccurrences:
    def __init__(
        self,
        project_root: Path,
        artifacts: tuple[tuple[ArtifactRef, bytes], ...],
    ) -> None:
        self.root = project_root
        self.artifacts = tuple(
            item
            for item in artifacts
            if item[0].kind == AcquisitionArtifact.EVIDENCE_HTTP_CONTENT.value
        )

    def validate(
        self,
        materials: tuple[MaterialRecord, ...],
        attempts: tuple[RetrievalAttempt, ...],
    ) -> None:
        references = tuple(reference for attempt in attempts for reference in attempt.content_refs)
        for reference in references:
            self._resolve(reference)
        attempts_by_material = {
            material_id: attempt for attempt in attempts for material_id in attempt.material_ids
        }
        external_materials = tuple(
            material for material in materials if isinstance(material.origin, ExternalUrlOrigin)
        )
        for material in external_materials:
            attempt = attempts_by_material[material.identifier]
            responses = (material.origin.response, *material.origin.corroboration)
            origin_refs = tuple(response.content_ref for response in responses)
            if attempt.outcome is not RetrievalOutcome.RETRIEVED:
                raise WorkflowError("external material is not owned by a successful retrieval")
            if origin_refs != attempt.content_refs:
                raise WorkflowError(
                    "external material provenance differs from its exact HTTP occurrences"
                )
            for response in responses:
                self._validate_response(response)
            primary_artifact, _ = self._resolve(material.origin.response.content_ref)
            expected_path = self.root / ".dpf" / "cas" / primary_artifact.cas_path
            if (self.root / material.path).resolve() != expected_path.resolve():
                raise WorkflowError("external material path is not its canonical core CAS object")

    def _validate_response(self, response: HttpResponseRecord) -> None:
        artifact, data = self._resolve(response.content_ref)
        if (
            artifact.digest != response.sha256
            or artifact.size != response.size_bytes
            or artifact.source != response.resolved_url
            or hashlib.sha256(data).hexdigest() != response.sha256
        ):
            raise WorkflowError("HTTP response provenance differs from its CAS occurrence")

    def _resolve(self, reference: EvidenceContentRef) -> tuple[ArtifactRef, bytes]:
        matches = [item for item in self.artifacts if reference.matches(item[0])]
        if len(matches) != 1:
            raise WorkflowError(
                "HTTP evidence reference does not resolve to one exact auxiliary occurrence"
            )
        return matches[0]


def validate_http_content_occurrences(
    context: BundleValidationContext,
    materials: tuple[MaterialRecord, ...],
    attempts: tuple[RetrievalAttempt, ...],
) -> None:
    EvidenceContentOccurrences(
        context.project_root,
        context.current_stage_artifacts,
    ).validate(materials, attempts)
