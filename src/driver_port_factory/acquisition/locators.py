from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from ..core.models import WorkflowError
from .authority import (
    AuthorityBasis,
    parse_external_authority,
)
from .facets import LocatorKind, MaterialRedistribution
from .parsing import byte_limit, exact_object, http_url, nonempty, relative_path, sha256
from .repository_role import RepositoryRole


@dataclass(frozen=True, slots=True)
class MaterialPolicy:
    license_note: str
    redistribution: MaterialRedistribution
    original: bool
    derived_from: str | None = None
    original_path: str | None = None
    page_map: str | None = None

    @classmethod
    def from_dict(cls, value: object) -> MaterialPolicy:
        candidate = exact_object(
            value,
            required={"license", "redistribution", "original"},
            optional={
                "kind",
                "repository",
                "path",
                "source_url",
                "revision",
                "expected_sha256",
                "max_bytes",
                "authority",
                "derived_from",
                "original_path",
                "page_map",
            },
            label="evidence locator",
        )
        try:
            redistribution = MaterialRedistribution(candidate["redistribution"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("evidence locator has invalid redistribution policy") from error
        original = candidate["original"]
        if not isinstance(original, bool):
            raise WorkflowError("evidence locator original must be boolean")
        derivative = {"derived_from", "original_path", "page_map"}
        present = derivative & candidate.keys()
        if original:
            if present:
                raise WorkflowError("original evidence must not declare derivative linkage")
            return cls(
                nonempty(candidate["license"], "evidence locator license"),
                redistribution,
                True,
            )
        if present != derivative:
            raise WorkflowError("derived evidence requires complete derivative linkage")
        return cls(
            nonempty(candidate["license"], "evidence locator license"),
            redistribution,
            False,
            nonempty(candidate["derived_from"], "derived_from"),
            relative_path(candidate["original_path"], "original evidence path"),
            nonempty(candidate["page_map"], "page_map"),
        )

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "license": self.license_note,
            "redistribution": self.redistribution.value,
            "original": self.original,
        }
        if not self.original:
            value.update(
                {
                    "derived_from": self.derived_from,
                    "original_path": self.original_path,
                    "page_map": self.page_map,
                }
            )
        return value


@dataclass(frozen=True, slots=True)
class GitBlobLocator:
    repository: RepositoryRole
    path: str
    policy: MaterialPolicy
    kind = LocatorKind.GIT_BLOB

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "repository": self.repository.value,
            "path": self.path,
            **self.policy.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ExternalUrlLocator:
    source_url: str
    revision: str
    expected_sha256: str
    max_bytes: int
    authority: AuthorityBasis
    policy: MaterialPolicy
    kind = LocatorKind.EXTERNAL_URL

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "source_url": self.source_url,
            "revision": self.revision,
            "expected_sha256": self.expected_sha256,
            "max_bytes": self.max_bytes,
            "authority": self.authority.to_dict(),
            **self.policy.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ExternalReferenceLocator:
    source_url: str
    max_bytes: int
    kind = LocatorKind.EXTERNAL_REFERENCE

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "source_url": self.source_url,
            "max_bytes": self.max_bytes,
        }


EvidenceLocator: TypeAlias = GitBlobLocator | ExternalUrlLocator | ExternalReferenceLocator


def parse_locator(value: object) -> EvidenceLocator:
    candidate = exact_object(
        value,
        required={"kind"},
        optional={
            "repository",
            "path",
            "source_url",
            "revision",
            "expected_sha256",
            "max_bytes",
            "authority",
            "license",
            "redistribution",
            "original",
            "derived_from",
            "original_path",
            "page_map",
        },
        label="evidence locator",
    )
    try:
        kind = LocatorKind(candidate["kind"])
    except (TypeError, ValueError) as error:
        raise WorkflowError("evidence locator has an invalid kind") from error
    if kind is LocatorKind.EXTERNAL_REFERENCE:
        exact_object(
            candidate,
            required={"kind", "source_url", "max_bytes"},
            label="external reference locator",
        )
        return ExternalReferenceLocator(
            http_url(candidate["source_url"], "external reference URL"),
            byte_limit(candidate["max_bytes"], "external reference max_bytes"),
        )
    policy = MaterialPolicy.from_dict(candidate)
    if kind is LocatorKind.GIT_BLOB:
        exact_object(
            candidate,
            required={"kind", "repository", "path", *policy.to_dict()},
            label="Git evidence locator",
        )
        try:
            repository = RepositoryRole(candidate["repository"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("Git evidence locator has an invalid repository role") from error
        return GitBlobLocator(
            repository,
            relative_path(candidate["path"], "Git evidence locator path"),
            policy,
        )
    exact_object(
        candidate,
        required={
            "kind",
            "source_url",
            "revision",
            "expected_sha256",
            "max_bytes",
            "authority",
            *policy.to_dict(),
        },
        label="external URL locator",
    )
    return ExternalUrlLocator(
        http_url(candidate["source_url"], "external evidence source_url"),
        nonempty(candidate["revision"], "external evidence revision"),
        sha256(candidate["expected_sha256"], "external URL expected SHA256"),
        byte_limit(candidate["max_bytes"], "external URL max_bytes"),
        parse_external_authority(candidate["authority"]),
        policy,
    )
