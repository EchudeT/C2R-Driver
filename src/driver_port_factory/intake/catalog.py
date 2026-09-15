from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.models import WorkflowError
from .contracts import ResolutionMatch


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", " ", normalized, flags=re.UNICODE).strip()


@dataclass(frozen=True, slots=True)
class DriverCandidate:
    candidate_id: str
    canonical_name: str
    source_entry_hint: str
    device_family: str
    bus_or_transport: str
    aliases: tuple[str, ...]
    device_scope: tuple[str, ...]
    candidate_device_ids: tuple[str, ...] = ()
    qemu_models: tuple[str, ...] = ()
    evidence_hints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in (
            "aliases",
            "device_scope",
            "candidate_device_ids",
            "qemu_models",
            "evidence_hints",
        ):
            value[key] = list(value[key])
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DriverCandidate:
        data = dict(value)
        for key in (
            "aliases",
            "device_scope",
            "candidate_device_ids",
            "qemu_models",
            "evidence_hints",
        ):
            data[key] = tuple(data.get(key, ()))
        return cls(**data)


@dataclass(frozen=True, slots=True)
class Resolution:
    query: str
    candidates: tuple[DriverCandidate, ...]
    match_type: ResolutionMatch
    auto_confirmable: bool
    metadata_sources: tuple[MetadataSource, ...] = ()


@dataclass(frozen=True, slots=True)
class MetadataSource:
    provider_id: str
    version: int
    digest: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DriverCatalog:
    def __init__(
        self,
        source_platform: str,
        candidates: tuple[DriverCandidate, ...],
        *,
        catalog_id: str,
        catalog_version: int,
        digest: str,
        source: str,
    ) -> None:
        self.source_platform = source_platform
        self.candidates = candidates
        self.catalog_id = catalog_id
        self.catalog_version = catalog_version
        self.digest = digest
        self.source = source

    @classmethod
    def from_file(cls, path: Path) -> DriverCatalog:
        path = path.resolve()
        try:
            data = path.read_bytes()
            value = json.loads(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError(f"cannot read driver catalog {path}: {error}") from error
        if not isinstance(value, dict) or not isinstance(value.get("drivers"), list):
            raise WorkflowError(f"invalid driver catalog: {path}")
        return cls(
            source_platform=str(value["source_platform"]),
            candidates=tuple(DriverCandidate.from_dict(item) for item in value["drivers"]),
            catalog_id=str(value.get("catalog_id", path.name)),
            catalog_version=int(value.get("catalog_version", value.get("schema_version", 1))),
            digest=hashlib.sha256(data).hexdigest(),
            source=str(path),
        )

    @property
    def metadata_source(self) -> MetadataSource:
        return MetadataSource(
            provider_id=self.catalog_id,
            version=self.catalog_version,
            digest=self.digest,
            source=self.source,
        )

    def resolve(self, query: str) -> Resolution:
        normalized_query = normalize_name(query)
        exact: list[DriverCandidate] = []
        fuzzy: list[DriverCandidate] = []
        query_tokens = set(normalized_query.split())
        for candidate in self.candidates:
            names = (
                candidate.canonical_name,
                candidate.source_entry_hint,
                *candidate.aliases,
            )
            normalized_names = {normalize_name(name) for name in names}
            if normalized_query in normalized_names:
                exact.append(candidate)
                continue
            candidate_tokens = set().union(*(name.split() for name in normalized_names))
            if (
                normalized_query
                and any(
                    normalized_query in name or name in normalized_query
                    for name in normalized_names
                )
                or query_tokens
                and len(query_tokens & candidate_tokens) >= min(2, len(query_tokens))
            ):
                fuzzy.append(candidate)
        if exact:
            return Resolution(
                query,
                tuple(exact),
                ResolutionMatch.EXACT,
                len(exact) == 1,
                (self.metadata_source,),
            )
        if fuzzy:
            return Resolution(
                query,
                tuple(fuzzy),
                ResolutionMatch.FUZZY,
                False,
                (self.metadata_source,),
            )
        return Resolution(
            query,
            (),
            ResolutionMatch.NO_MATCH,
            False,
            (self.metadata_source,),
        )
