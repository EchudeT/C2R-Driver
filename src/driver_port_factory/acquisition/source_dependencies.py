from __future__ import annotations

import hashlib
import posixpath
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from ..core.ledger import canonical_json
from ..core.models import WorkflowError
from .accounting import RetrievalAttempt
from .facets import FacetDisposition, RetrievalOutcome
from .locators import GitBlobLocator
from .material import GitBlobOrigin, MaterialRecord
from .parsing import exact_object, nonempty, object_id, schema_version, sha256
from .repository_role import RepositoryRole

_QUOTED_INCLUDE = re.compile(r'^\s*#\s*include\s*"([^"\n]+)"', re.MULTILINE)


class DependencyScannerVersion(StrEnum):
    QUOTED_INCLUDE_V1 = "quoted-include-v1"


class DependencyIncludeForm(StrEnum):
    QUOTED_ONLY = "quoted-only"


class IncludeResolutionRule(StrEnum):
    INCLUDING_DIRECTORY = "including-directory"


class DependencyInventoryScope(StrEnum):
    INITIAL_ACQUISITION_QUOTED_INCLUDES = "initial-acquisition-quoted-includes"


@dataclass(frozen=True, slots=True)
class DependencyScanner:
    version: DependencyScannerVersion
    include_form: DependencyIncludeForm
    resolution_rule: IncludeResolutionRule

    @classmethod
    def current(cls) -> DependencyScanner:
        return cls(
            DependencyScannerVersion.QUOTED_INCLUDE_V1,
            DependencyIncludeForm.QUOTED_ONLY,
            IncludeResolutionRule.INCLUDING_DIRECTORY,
        )

    @classmethod
    def from_dict(cls, value: object) -> DependencyScanner:
        candidate = exact_object(
            value,
            required={"version", "include_form", "resolution_rule"},
            label="initial dependency scanner",
        )
        try:
            return cls(
                DependencyScannerVersion(candidate["version"]),
                DependencyIncludeForm(candidate["include_form"]),
                IncludeResolutionRule(candidate["resolution_rule"]),
            )
        except (TypeError, ValueError) as error:
            raise WorkflowError("initial dependency scanner configuration is invalid") from error

    def to_dict(self) -> dict[str, str]:
        return {
            "version": self.version.value,
            "include_form": self.include_form.value,
            "resolution_rule": self.resolution_rule.value,
        }


@dataclass(frozen=True, slots=True)
class IncludeSite:
    including_path: str
    line: int
    spelling: str

    @classmethod
    def from_dict(cls, value: object) -> IncludeSite:
        candidate = exact_object(
            value,
            required={"including_path", "line", "spelling"},
            label="quoted include site",
        )
        line = candidate["line"]
        if not isinstance(line, int) or line <= 0:
            raise WorkflowError("quoted include site line must be positive")
        return cls(
            _repository_path(candidate["including_path"]),
            line,
            nonempty(candidate["spelling"], "quoted include spelling"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "including_path": self.including_path,
            "line": self.line,
            "spelling": self.spelling,
        }


@dataclass(frozen=True, slots=True)
class DependencyRequirement:
    identifier: str
    repository_path: str
    sites: tuple[IncludeSite, ...]
    disposition: FacetDisposition
    retrieval_attempt_id: str
    material_id: str | None
    gap_id: str | None

    @classmethod
    def from_dict(cls, value: object) -> DependencyRequirement:
        candidate = exact_object(
            value,
            required={
                "id",
                "repository_path",
                "sites",
                "disposition",
                "retrieval_attempt_id",
                "material_id",
                "gap_id",
            },
            label="initial dependency requirement",
        )
        raw_sites = candidate["sites"]
        if not isinstance(raw_sites, list) or not raw_sites:
            raise WorkflowError("initial dependency requirement requires include sites")
        try:
            disposition = FacetDisposition(candidate["disposition"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("initial dependency requirement disposition is invalid") from error
        material_id = candidate["material_id"]
        gap_id = candidate["gap_id"]
        if disposition is FacetDisposition.CONTROLLED:
            if not isinstance(material_id, str) or not material_id or gap_id is not None:
                raise WorkflowError("controlled dependency requires one material and no gap")
        elif not isinstance(gap_id, str) or not gap_id or material_id is not None:
            raise WorkflowError("missing dependency requires one gap and no material")
        return cls(
            nonempty(candidate["id"], "initial dependency requirement ID"),
            _repository_path(candidate["repository_path"]),
            tuple(IncludeSite.from_dict(site) for site in raw_sites),
            disposition,
            nonempty(candidate["retrieval_attempt_id"], "dependency retrieval attempt ID"),
            material_id,
            gap_id,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.identifier,
            "repository_path": self.repository_path,
            "sites": [site.to_dict() for site in self.sites],
            "disposition": self.disposition.value,
            "retrieval_attempt_id": self.retrieval_attempt_id,
            "material_id": self.material_id,
            "gap_id": self.gap_id,
        }


@dataclass(frozen=True, slots=True)
class InitialDependencyInventory:
    scope: DependencyInventoryScope
    entry_material_id: str
    entry_commit: str
    entry_blob: str
    entry_sha256: str
    scanner: DependencyScanner
    requirements: tuple[DependencyRequirement, ...]
    schema_version = 1

    @classmethod
    def from_dict(cls, value: object) -> InitialDependencyInventory:
        candidate = exact_object(
            value,
            required={
                "schema_version",
                "scope",
                "entry_material_id",
                "entry_commit",
                "entry_blob",
                "entry_sha256",
                "scanner",
                "requirements",
            },
            label="initial quoted-include dependency inventory",
        )
        schema_version(candidate, "initial quoted-include dependency inventory")
        try:
            scope = DependencyInventoryScope(candidate["scope"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("initial dependency inventory has an invalid scope") from error
        raw_requirements = candidate["requirements"]
        if not isinstance(raw_requirements, list):
            raise WorkflowError("initial dependency inventory requirements must be a list")
        requirements = tuple(
            DependencyRequirement.from_dict(requirement) for requirement in raw_requirements
        )
        paths = [requirement.repository_path for requirement in requirements]
        if len(set(paths)) != len(paths):
            raise WorkflowError("initial dependency inventory paths must be unique")
        return cls(
            scope,
            nonempty(candidate["entry_material_id"], "dependency inventory entry material"),
            object_id(candidate["entry_commit"], "dependency inventory entry commit"),
            object_id(candidate["entry_blob"], "dependency inventory entry blob"),
            sha256(candidate["entry_sha256"], "dependency inventory entry SHA256"),
            DependencyScanner.from_dict(candidate["scanner"]),
            requirements,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "scope": self.scope.value,
            "entry_material_id": self.entry_material_id,
            "entry_commit": self.entry_commit,
            "entry_blob": self.entry_blob,
            "entry_sha256": self.entry_sha256,
            "scanner": self.scanner.to_dict(),
            "requirements": [requirement.to_dict() for requirement in self.requirements],
        }


@dataclass(slots=True)
class _RequirementDraft:
    attempt: RetrievalAttempt
    material: MaterialRecord | None
    sites: list[IncludeSite]


class _DependencyInventoryBuilder:
    def __init__(
        self,
        root: Path,
        dependencies: tuple[MaterialRecord, ...],
        attempts: tuple[RetrievalAttempt, ...],
    ) -> None:
        self.root = root
        self.materials = {record.identifier: record for record in dependencies}
        self.attempts = self._index_attempts(attempts)
        self.requirements: dict[str, _RequirementDraft] = {}

    def build(self, entry: MaterialRecord) -> InitialDependencyInventory:
        entry_origin = self._source_origin(entry)
        pending = [entry]
        scanned: set[str] = set()
        while pending:
            material = pending.pop(0)
            origin = self._source_origin(material)
            if origin.path in scanned:
                continue
            scanned.add(origin.path)
            for site, dependency_path in _quoted_includes(
                origin.path,
                self._verified_bytes(material),
            ):
                draft = self._requirement(dependency_path, pending)
                draft.sites.append(site)
        orphan_paths = sorted(set(self.attempts) - set(self.requirements))
        if orphan_paths:
            raise WorkflowError(
                "source dependency acquisition contains unreferenced paths: "
                + ", ".join(orphan_paths)
            )
        records = tuple(
            _requirement(
                path,
                self.requirements[path].attempt,
                self.requirements[path].material,
                self.requirements[path].sites,
            )
            for path in sorted(self.requirements)
        )
        return InitialDependencyInventory(
            DependencyInventoryScope.INITIAL_ACQUISITION_QUOTED_INCLUDES,
            entry.identifier,
            entry_origin.commit,
            entry_origin.blob,
            entry.sha256,
            DependencyScanner.current(),
            records,
        )

    def _requirement(
        self,
        dependency_path: str,
        pending: list[MaterialRecord],
    ) -> _RequirementDraft:
        current = self.requirements.get(dependency_path)
        if current is not None:
            return current
        attempt = self.attempts.get(dependency_path)
        if attempt is None:
            raise WorkflowError(
                f"initial quoted include is absent from the acquisition plan: {dependency_path}"
            )
        material = self._retrieved_material(dependency_path, attempt)
        if material is not None:
            pending.append(material)
        current = _RequirementDraft(attempt, material, [])
        self.requirements[dependency_path] = current
        return current

    def _retrieved_material(
        self,
        dependency_path: str,
        attempt: RetrievalAttempt,
    ) -> MaterialRecord | None:
        if attempt.outcome is not RetrievalOutcome.RETRIEVED:
            return None
        if len(attempt.material_ids) != 1:
            raise WorkflowError("retrieved source dependency must bind one material")
        material = self.materials.get(attempt.material_ids[0])
        if material is None:
            raise WorkflowError("source dependency attempt references missing material")
        if self._source_origin(material).path != dependency_path:
            raise WorkflowError("source dependency material differs from its requirement")
        return material

    def _verified_bytes(self, material: MaterialRecord) -> bytes:
        data = (self.root / material.path).read_bytes()
        if hashlib.sha256(data).hexdigest() != material.sha256:
            raise WorkflowError(f"source dependency material bytes drifted: {material.identifier}")
        return data

    @staticmethod
    def _source_origin(material: MaterialRecord) -> GitBlobOrigin:
        origin = material.origin
        if not isinstance(origin, GitBlobOrigin) or origin.repository is not RepositoryRole.SOURCE:
            raise WorkflowError("initial source dependency material is not a frozen source blob")
        return origin

    @staticmethod
    def _index_attempts(
        attempts: tuple[RetrievalAttempt, ...],
    ) -> dict[str, RetrievalAttempt]:
        indexed: dict[str, RetrievalAttempt] = {}
        for attempt in attempts:
            locator = attempt.locator
            if (
                not isinstance(locator, GitBlobLocator)
                or locator.repository is not RepositoryRole.SOURCE
            ):
                raise WorkflowError("initial source dependency locators must be source Git blobs")
            if locator.path in indexed:
                raise WorkflowError(f"duplicate source dependency locator: {locator.path}")
            indexed[locator.path] = attempt
        return indexed


def build_initial_dependency_inventory(
    root: Path,
    entry: MaterialRecord,
    dependencies: tuple[MaterialRecord, ...],
    attempts: tuple[RetrievalAttempt, ...],
) -> InitialDependencyInventory:
    return _DependencyInventoryBuilder(root, dependencies, attempts).build(entry)


def _quoted_includes(
    including_path: str,
    data: bytes,
) -> tuple[tuple[IncludeSite, str], ...]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkflowError(
            f"quoted-include inventory cannot decode source: {including_path}"
        ) from error
    found = []
    for match in _QUOTED_INCLUDE.finditer(text):
        spelling = match.group(1)
        line = text.count("\n", 0, match.start()) + 1
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(including_path), spelling))
        found.append((IncludeSite(including_path, line, spelling), _repository_path(resolved)))
    return tuple(found)


def _requirement(
    repository_path: str,
    attempt: RetrievalAttempt,
    material: MaterialRecord | None,
    sites: list[IncludeSite],
) -> DependencyRequirement:
    identity = hashlib.sha256(
        canonical_json(
            {
                "repository_path": repository_path,
                "sites": [site.to_dict() for site in sorted(sites, key=_site_key)],
            }
        ).encode("utf-8")
    ).hexdigest()[:16]
    identifier = f"source-dependency.{identity}"
    ordered_sites = tuple(sorted(sites, key=_site_key))
    if material is not None:
        return DependencyRequirement(
            identifier,
            repository_path,
            ordered_sites,
            FacetDisposition.CONTROLLED,
            attempt.identifier,
            material.identifier,
            None,
        )
    return DependencyRequirement(
        identifier,
        repository_path,
        ordered_sites,
        FacetDisposition.EXPLICIT_GAP,
        attempt.identifier,
        None,
        f"gap.{identifier}",
    )


def _site_key(site: IncludeSite) -> tuple[str, int, str]:
    return site.including_path, site.line, site.spelling


def _repository_path(value: object) -> str:
    path = nonempty(value, "source repository path")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or path in {"", "."} or ".." in candidate.parts:
        raise WorkflowError(f"source repository path escapes the frozen tree: {path}")
    return candidate.as_posix()
