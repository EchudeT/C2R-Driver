from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeAlias

from ..core.models import ArtifactRef, WorkflowError
from .authority import (
    AuthorityBasis,
    CorroboratedAuthority,
    EvidenceAuthority,
    parse_external_authority,
)
from .contracts import AcquisitionArtifact
from .facets import EvidenceFacet, MaterialOriginKind, MaterialRedistribution, parse_facet
from .parsing import exact_object, nonempty, object_id, sha256
from .repository_role import RepositoryRole


@dataclass(frozen=True, slots=True)
class EvidenceContentRef:
    digest: str
    ordinal: int
    source: str

    @classmethod
    def from_dict(cls, value: object) -> EvidenceContentRef:
        candidate = exact_object(
            value,
            required={"kind", "digest", "ordinal", "source"},
            label="evidence content reference",
        )
        if candidate["kind"] != AcquisitionArtifact.EVIDENCE_HTTP_CONTENT.value:
            raise WorkflowError("evidence content reference has an invalid kind")
        ordinal = candidate["ordinal"]
        if not isinstance(ordinal, int) or ordinal < 0:
            raise WorkflowError("evidence content reference has an invalid ordinal")
        return cls(
            sha256(candidate["digest"], "evidence content reference digest"),
            ordinal,
            nonempty(candidate["source"], "evidence content reference source"),
        )

    @classmethod
    def from_artifact(cls, artifact: ArtifactRef) -> EvidenceContentRef:
        if artifact.kind != AcquisitionArtifact.EVIDENCE_HTTP_CONTENT.value:
            raise WorkflowError("evidence content occurrence has an invalid artifact kind")
        if artifact.ordinal is None:
            raise WorkflowError("evidence content occurrence has no persisted ordinal")
        return cls(artifact.digest, artifact.ordinal, artifact.source)

    def matches(self, artifact: ArtifactRef) -> bool:
        return (
            artifact.kind == AcquisitionArtifact.EVIDENCE_HTTP_CONTENT.value
            and artifact.digest == self.digest
            and artifact.ordinal == self.ordinal
            and artifact.source == self.source
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": AcquisitionArtifact.EVIDENCE_HTTP_CONTENT.value,
            "digest": self.digest,
            "ordinal": self.ordinal,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class GitBlobOrigin:
    repository: RepositoryRole
    commit: str
    blob: str
    path: str
    kind = MaterialOriginKind.GIT_BLOB

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "repository": self.repository.value,
            "commit": self.commit,
            "blob": self.blob,
            "path": self.path,
        }


@dataclass(frozen=True, slots=True)
class HttpResponseRecord:
    requested_url: str
    resolved_url: str
    sha256: str
    size_bytes: int
    media_type: str
    retrieved_at: str
    content_ref: EvidenceContentRef

    @classmethod
    def from_dict(cls, value: object) -> HttpResponseRecord:
        candidate = exact_object(
            value,
            required={
                "requested_url",
                "resolved_url",
                "sha256",
                "size_bytes",
                "media_type",
                "retrieved_at",
                "content_ref",
            },
            label="HTTP response provenance",
        )
        size = candidate["size_bytes"]
        if not isinstance(size, int) or size <= 0:
            raise WorkflowError("HTTP response provenance requires non-empty content")
        response_sha256 = sha256(candidate["sha256"], "HTTP response SHA256")
        content_ref = EvidenceContentRef.from_dict(candidate["content_ref"])
        if response_sha256 != content_ref.digest:
            raise WorkflowError("HTTP response differs from its CAS content reference")
        return cls(
            nonempty(candidate["requested_url"], "HTTP requested URL"),
            nonempty(candidate["resolved_url"], "HTTP resolved URL"),
            response_sha256,
            size,
            _media_type(candidate["media_type"]),
            nonempty(candidate["retrieved_at"], "HTTP retrieval timestamp"),
            content_ref,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "requested_url": self.requested_url,
            "resolved_url": self.resolved_url,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "retrieved_at": self.retrieved_at,
            "content_ref": self.content_ref.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ExternalUrlOrigin:
    source_url: str
    revision: str
    authority_basis: AuthorityBasis
    authority: EvidenceAuthority
    response: HttpResponseRecord
    corroboration: tuple[HttpResponseRecord, ...]
    kind = MaterialOriginKind.EXTERNAL_URL

    def __post_init__(self) -> None:
        if self.response.requested_url != self.source_url:
            raise WorkflowError("external origin response does not bind its source URL")
        if isinstance(self.authority_basis, CorroboratedAuthority):
            if self.authority is not EvidenceAuthority.CORROBORATED:
                raise WorkflowError("corroborated authority basis has inconsistent status")
            if len(self.corroboration) != len(self.authority_basis.sources):
                raise WorkflowError("external origin lacks corroboration retrievals")
        elif self.authority is not EvidenceAuthority.PRIMARY or self.corroboration:
            raise WorkflowError("repository-origin evidence has inconsistent authority status")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "source_url": self.source_url,
            "revision": self.revision,
            "authority_basis": self.authority_basis.to_dict(),
            "authority": self.authority.value,
            "response": self.response.to_dict(),
            "corroboration": [record.to_dict() for record in self.corroboration],
        }


MaterialOrigin: TypeAlias = GitBlobOrigin | ExternalUrlOrigin


@dataclass(frozen=True, slots=True)
class MaterialRecord:
    identifier: str
    facet: EvidenceFacet
    path: str
    source_url: str
    revision: str
    acquired_at: str
    license_note: str
    redistribution: MaterialRedistribution
    sha256: str
    size_bytes: int
    media_type: str
    original: bool
    index: bool
    origin: MaterialOrigin
    derived_from: str | None = None
    original_path: str | None = None
    page_map: str | None = None
    category: str | None = None
    notes: str | None = None

    @classmethod
    def from_dict(cls, value: object) -> MaterialRecord:
        candidate = exact_object(
            value,
            required={
                "id",
                "domain",
                "facet",
                "path",
                "source_url",
                "revision",
                "acquired_at",
                "license",
                "redistribution",
                "sha256",
                "size_bytes",
                "media_type",
                "original",
                "index",
                "origin",
            },
            optional={"derived_from", "original_path", "page_map", "category", "notes"},
            label="controlled material",
        )
        try:
            redistribution = MaterialRedistribution(candidate["redistribution"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("controlled material has invalid redistribution policy") from error
        size = candidate["size_bytes"]
        if not isinstance(size, int) or size <= 0:
            raise WorkflowError("controlled material must contain at least one verified byte")
        original = candidate["original"]
        indexed = candidate["index"]
        if not isinstance(original, bool) or not isinstance(indexed, bool):
            raise WorkflowError("controlled material original/index flags must be boolean")
        derivative = tuple(candidate.get(field) for field in _DERIVATIVE_FIELDS)
        if original and any(item is not None for item in derivative):
            raise WorkflowError("original material cannot declare derivative linkage")
        if not original and not all(isinstance(item, str) and item for item in derivative):
            raise WorkflowError("derived material requires parent, original path, and page map")
        origin = parse_origin(candidate["origin"])
        source_url = nonempty(candidate["source_url"], "controlled material source_url")
        revision = nonempty(candidate["revision"], "controlled material revision")
        if isinstance(origin, GitBlobOrigin):
            if revision != origin.commit:
                raise WorkflowError("Git material revision differs from its origin commit")
        elif isinstance(origin, ExternalUrlOrigin) and (source_url, revision) != (
            origin.source_url,
            origin.revision,
        ):
            raise WorkflowError("external material provenance differs from its origin")
        return cls(
            _identifier(candidate["id"], "controlled material ID"),
            parse_facet(candidate["domain"], candidate["facet"]),
            nonempty(candidate["path"], "controlled material path"),
            source_url,
            revision,
            nonempty(candidate["acquired_at"], "controlled material acquired_at"),
            nonempty(candidate["license"], "controlled material license"),
            redistribution,
            sha256(candidate["sha256"], "controlled material SHA256"),
            size,
            _media_type(candidate["media_type"]),
            original,
            indexed,
            origin,
            derivative[0] if isinstance(derivative[0], str) else None,
            derivative[1] if isinstance(derivative[1], str) else None,
            derivative[2] if isinstance(derivative[2], str) else None,
            _optional(candidate.get("category"), "controlled material category"),
            _optional(candidate.get("notes"), "controlled material notes"),
        )

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "id": self.identifier,
            "domain": self.facet.lane.value,
            "facet": self.facet.name.value,
            "path": self.path,
            "source_url": self.source_url,
            "revision": self.revision,
            "acquired_at": self.acquired_at,
            "license": self.license_note,
            "redistribution": self.redistribution.value,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "original": self.original,
            "index": self.index,
            "origin": self.origin.to_dict(),
        }
        for key, item in (
            ("derived_from", self.derived_from),
            ("original_path", self.original_path),
            ("page_map", self.page_map),
            ("category", self.category),
            ("notes", self.notes),
        ):
            if item is not None:
                value[key] = item
        return value


def parse_materials(data: bytes) -> tuple[MaterialRecord, ...]:
    try:
        raw = [json.loads(line) for line in data.decode("utf-8").splitlines() if line]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError("materials_manifest must be JSON Lines") from error
    records = tuple(MaterialRecord.from_dict(item) for item in raw)
    if not records:
        raise WorkflowError("materials_manifest requires controlled content")
    if len({record.identifier for record in records}) != len(records):
        raise WorkflowError("materials_manifest contains duplicate IDs")
    return records


def parse_origin(value: object) -> MaterialOrigin:
    candidate = exact_object(
        value,
        required={"kind"},
        optional={
            "repository",
            "commit",
            "blob",
            "path",
            "source_url",
            "revision",
            "authority_basis",
            "authority",
            "response",
            "corroboration",
        },
        label="controlled material origin",
    )
    try:
        kind = MaterialOriginKind(candidate["kind"])
    except (TypeError, ValueError) as error:
        raise WorkflowError("controlled material has invalid origin kind") from error
    if kind is MaterialOriginKind.GIT_BLOB:
        exact_object(
            candidate,
            required={"kind", "repository", "commit", "blob", "path"},
            label="Git material origin",
        )
        try:
            repository = RepositoryRole(candidate["repository"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("Git material origin has invalid repository role") from error
        return GitBlobOrigin(
            repository,
            object_id(candidate["commit"], "Git material commit"),
            object_id(candidate["blob"], "Git material blob"),
            nonempty(candidate["path"], "Git material path"),
        )
    exact_object(
        candidate,
        required={
            "kind",
            "source_url",
            "revision",
            "authority_basis",
            "authority",
            "response",
            "corroboration",
        },
        label="external URL material origin",
    )
    try:
        authority = EvidenceAuthority(candidate["authority"])
    except (TypeError, ValueError) as error:
        raise WorkflowError("external material origin has invalid authority") from error
    raw_corroboration = candidate["corroboration"]
    if not isinstance(raw_corroboration, list):
        raise WorkflowError("external material corroboration must be a list")
    return ExternalUrlOrigin(
        nonempty(candidate["source_url"], "external material source_url"),
        nonempty(candidate["revision"], "external material revision"),
        parse_external_authority(candidate["authority_basis"]),
        authority,
        HttpResponseRecord.from_dict(candidate["response"]),
        tuple(HttpResponseRecord.from_dict(item) for item in raw_corroboration),
    )


_DERIVATIVE_FIELDS = ("derived_from", "original_path", "page_map")


def _identifier(value: object, label: str) -> str:
    text = nonempty(value, label)
    if not all(character.isalnum() or character in "._-" for character in text):
        raise WorkflowError(f"{label} contains unsupported characters")
    return text


def _media_type(value: object) -> str:
    media_type = nonempty(value, "controlled material media type")
    if ";" in media_type or "/" not in media_type:
        raise WorkflowError("controlled material has invalid media type")
    return media_type


def _optional(value: object, label: str) -> str | None:
    return None if value is None else nonempty(value, label)
