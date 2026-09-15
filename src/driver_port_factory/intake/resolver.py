from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..core.models import WorkflowError
from .catalog import DriverCandidate, DriverCatalog, MetadataSource, Resolution


@dataclass(frozen=True, slots=True)
class DriverRequest:
    source_platform: str
    target_platform: str
    driver_name: str
    raw_request: str


class DriverMetadataProvider(Protocol):
    """A lightweight, provenance-bearing source of driver identity metadata."""

    source_platform: str

    @property
    def metadata_source(self) -> MetadataSource: ...

    def resolve(self, query: str) -> Resolution: ...


class SourceDriverResolver(Protocol):
    """Resolve a user request without acquiring a full source repository."""

    def resolve(self, request: DriverRequest) -> Resolution: ...


class CompositeSourceDriverResolver:
    """Merge any number of platform metadata providers without driver-specific logic."""

    def __init__(
        self,
        source_platform: str,
        providers: Sequence[DriverMetadataProvider] = (),
    ) -> None:
        self.source_platform = source_platform
        self.providers = tuple(providers)
        mismatched = [
            provider.source_platform
            for provider in self.providers
            if provider.source_platform.casefold() != source_platform.casefold()
        ]
        if mismatched:
            raise WorkflowError(
                f"metadata provider platform does not match {source_platform}: "
                + ", ".join(mismatched)
            )

    @classmethod
    def from_catalogs(
        cls, source_platform: str, catalog_paths: Sequence[Path]
    ) -> CompositeSourceDriverResolver:
        return cls(
            source_platform,
            tuple(DriverCatalog.from_file(path) for path in catalog_paths),
        )

    def resolve(self, request: DriverRequest) -> Resolution:
        if request.source_platform.casefold() != self.source_platform.casefold():
            raise WorkflowError(
                f"resolver platform {self.source_platform} does not match request "
                f"{request.source_platform}"
            )
        resolutions = [provider.resolve(request.driver_name) for provider in self.providers]
        exact = self._deduplicate(
            candidate
            for resolution in resolutions
            if resolution.match_type == "EXACT"
            for candidate in resolution.candidates
        )
        fuzzy = self._deduplicate(
            candidate
            for resolution in resolutions
            if resolution.match_type == "FUZZY"
            for candidate in resolution.candidates
        )
        all_metadata_sources = (
            source for resolution in resolutions for source in resolution.metadata_sources
        )
        metadata_sources = tuple(
            {
                (source.provider_id, source.version, source.digest): source
                for source in all_metadata_sources
            }.values()
        )
        if exact:
            return Resolution(
                request.driver_name,
                exact,
                "EXACT",
                len(exact) == 1,
                metadata_sources,
            )
        if fuzzy:
            return Resolution(
                request.driver_name,
                fuzzy,
                "FUZZY",
                False,
                metadata_sources,
            )
        return Resolution(
            request.driver_name,
            (),
            "NO_MATCH",
            False,
            metadata_sources,
        )

    @staticmethod
    def _deduplicate(candidates) -> tuple[DriverCandidate, ...]:
        by_identity: dict[tuple[str, str, str], DriverCandidate] = {}
        for candidate in candidates:
            key = (
                candidate.canonical_name,
                candidate.source_entry_hint,
                candidate.bus_or_transport,
            )
            by_identity.setdefault(key, candidate)
        return tuple(by_identity.values())


@dataclass(frozen=True, slots=True)
class SourceIdentityVerification:
    consistent: bool
    source_root: str
    source_entry: str
    observations: tuple[str, ...]
    conflicts: tuple[str, ...]


class PinnedSourceIdentityVerifier(Protocol):
    """Verify a frozen envelope against the subsequently pinned source tree."""

    def verify(
        self, envelope: dict[str, object], source_root: Path
    ) -> SourceIdentityVerification: ...


class SourceEntryVerifier:
    """Generic minimum verifier; platform plugins add device-table and bus checks."""

    def verify(self, envelope: dict[str, object], source_root: Path) -> SourceIdentityVerification:
        root = source_root.resolve()
        entry = str(envelope["source_driver_entry_or_repository_hint"])
        candidate = (root / entry).resolve()
        conflicts: list[str] = []
        observations: list[str] = []
        if root not in candidate.parents:
            conflicts.append("source entry escapes the pinned source root")
        elif not candidate.is_file():
            conflicts.append("frozen source entry does not exist in the pinned source tree")
        else:
            observations.append("frozen source entry exists in the pinned source tree")
        return SourceIdentityVerification(
            consistent=not conflicts,
            source_root=str(root),
            source_entry=entry,
            observations=tuple(observations),
            conflicts=tuple(conflicts),
        )
