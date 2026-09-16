from __future__ import annotations

import hashlib
from pathlib import Path

from ..core.models import WorkflowError
from .accounting import RetrievalAttempt
from .authority import CorroboratedAuthority, RepositoryEndorsementAuthority
from .facet_policy import external_authority_is_allowed, repository_is_authoritative
from .git_material_validation import validate_git_material_identity
from .locators import ExternalUrlLocator, GitBlobLocator
from .material import ExternalUrlOrigin, GitBlobOrigin, MaterialRecord
from .repository_endorsement_validation import validate_repository_endorsement
from .repository_filesystem import workspace_file
from .repository_manifest import RepositoryAcquisition
from .retrieval import material_identifier


def validate_material_integrity(
    root: Path,
    acquisition: RepositoryAcquisition,
    materials: tuple[MaterialRecord, ...],
    attempts: tuple[RetrievalAttempt, ...],
) -> None:
    attempts_by_material = {
        identifier: attempt for attempt in attempts for identifier in attempt.material_ids
    }
    records_by_id = {record.identifier: record for record in materials}
    for record in materials:
        path = workspace_file(root, record.path, "controlled material")
        data = path.read_bytes()
        if len(data) != record.size_bytes or hashlib.sha256(data).hexdigest() != record.sha256:
            raise WorkflowError(f"controlled material bytes drifted: {record.identifier}")
        attempt = attempts_by_material[record.identifier]
        if material_identifier(record.facet, attempt.locator) != record.identifier:
            raise WorkflowError("controlled material ID does not bind its locator")
        _validate_material_origin(root, acquisition, record, path, attempt)
        if not record.original:
            parent = records_by_id.get(record.derived_from or "")
            if parent is None or not parent.original:
                raise WorkflowError("derived material does not reference a controlled original")
            if record.original_path != parent.path:
                raise WorkflowError("derived material original_path differs from its parent")


def _validate_material_origin(
    root: Path,
    acquisition: RepositoryAcquisition,
    record: MaterialRecord,
    material_path: Path,
    attempt: RetrievalAttempt,
) -> None:
    origin = record.origin
    locator = attempt.locator
    if isinstance(origin, GitBlobOrigin) and isinstance(locator, GitBlobLocator):
        if not repository_is_authoritative(record.facet, origin.repository):
            raise WorkflowError("Git material repository is not authoritative for its facet")
        if origin.repository is not locator.repository or origin.path != locator.path:
            raise WorkflowError("Git material origin differs from its locator")
        validate_git_material_identity(root, acquisition, record, material_path, origin)
        return
    if isinstance(origin, ExternalUrlOrigin) and isinstance(locator, ExternalUrlLocator):
        _validate_external_origin(root, acquisition, record, origin, locator)
        return
    raise WorkflowError("controlled material origin kind differs from its retrieval locator")


def _validate_external_origin(
    root: Path,
    acquisition: RepositoryAcquisition,
    record: MaterialRecord,
    origin: ExternalUrlOrigin,
    locator: ExternalUrlLocator,
) -> None:
    if not external_authority_is_allowed(record.facet, origin.authority_basis):
        raise WorkflowError("external URL is not authoritative for its facet")
    if (origin.source_url, origin.revision, origin.authority_basis) != (
        locator.source_url,
        locator.revision,
        locator.authority,
    ):
        raise WorkflowError("external URL origin differs from its locator")
    if (
        record.source_url,
        record.revision,
        record.sha256,
        record.size_bytes,
        record.media_type,
    ) != (
        origin.source_url,
        origin.revision,
        origin.response.sha256,
        origin.response.size_bytes,
        origin.response.media_type,
    ):
        raise WorkflowError("external material top-level provenance differs from its response")
    authority = locator.authority
    if isinstance(authority, CorroboratedAuthority):
        expected = tuple((item.source_url, item.expected_sha256) for item in authority.sources)
        actual = tuple((item.requested_url, item.sha256) for item in origin.corroboration)
        if actual != expected:
            raise WorkflowError("external material corroboration differs from retrieval plan")
        return
    if isinstance(authority, RepositoryEndorsementAuthority):
        validate_repository_endorsement(root, acquisition, locator, authority)
        return
    raise WorkflowError("external material has an invalid authority basis")
