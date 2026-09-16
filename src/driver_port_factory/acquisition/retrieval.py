from __future__ import annotations

import hashlib
from functools import singledispatchmethod

from ..core.ledger import canonical_json
from ..core.project import Project
from .authority_verification import (
    CorroboratedAuthorityVerifier,
    ExternalAuthorityVerifier,
    RepositoryEndorsementVerifier,
)
from .external_material_retrieval import ExternalMaterialRetriever
from .facet_policy import validate_locator_authority
from .facets import EvidenceFacet
from .git_material_retrieval import GitMaterialRetriever
from .http_content import EvidenceHttpContentRecorder
from .locators import (
    EvidenceLocator,
    ExternalReferenceLocator,
    ExternalUrlLocator,
    GitBlobLocator,
)
from .repository_manifest import RepositoryAcquisition
from .retrieval_result import RetrievedMaterial


class EvidenceRetriever:
    def __init__(self, project: Project, acquisition: RepositoryAcquisition) -> None:
        content = EvidenceHttpContentRecorder(project)
        authority = ExternalAuthorityVerifier(
            RepositoryEndorsementVerifier(project.root, acquisition),
            CorroboratedAuthorityVerifier(content),
        )
        self.git = GitMaterialRetriever(project.root, acquisition)
        self.external = ExternalMaterialRetriever(project.root, content, authority)

    def retrieve(
        self,
        facet: EvidenceFacet,
        locator: EvidenceLocator,
        *,
        material_id: str,
    ) -> RetrievedMaterial:
        validate_locator_authority(facet, locator)
        return self._retrieve(locator, facet, material_id)

    @singledispatchmethod
    def _retrieve(
        self,
        locator: EvidenceLocator,
        facet: EvidenceFacet,
        material_id: str,
    ) -> RetrievedMaterial:
        raise TypeError(f"unsupported evidence locator type: {type(locator).__name__}")

    @_retrieve.register
    def _git_blob(
        self,
        locator: GitBlobLocator,
        facet: EvidenceFacet,
        material_id: str,
    ) -> RetrievedMaterial:
        return self.git.retrieve(facet, locator, material_id)

    @_retrieve.register
    def _external_url(
        self,
        locator: ExternalUrlLocator,
        facet: EvidenceFacet,
        material_id: str,
    ) -> RetrievedMaterial:
        return self.external.retrieve_url(facet, locator, material_id)

    @_retrieve.register
    def _external_reference(
        self,
        locator: ExternalReferenceLocator,
        facet: EvidenceFacet,
        material_id: str,
    ) -> RetrievedMaterial:
        return self.external.retrieve_reference(locator)


def material_identifier(facet: EvidenceFacet, locator: EvidenceLocator) -> str:
    canonical = canonical_json(locator.to_dict()).encode("utf-8")
    suffix = hashlib.sha256(canonical).hexdigest()[:16]
    return f"{facet.lane.value}.{facet.name.value}.{suffix}"
