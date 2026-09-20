from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ijson

from ..core.models import WorkflowError
from ..knowledge.index import file_sha256
from .ast_index import _ClangSourceLocations, _ResolvedLocations
from .ast_scope import DeclarationScope
from .semantic_model import ClangNodeKind


@dataclass(frozen=True, slots=True)
class ClosureFile:
    relative_path: str
    path: Path
    sha256: str


class ClosureFileSet:
    """Resolve Clang locations only to files frozen by the source-closure manifest."""

    def __init__(self, source_root: Path, files: tuple[ClosureFile, ...],
                 source_paths: frozenset[str] | None = None) -> None:
        self.source_root = source_root.resolve()
        self.files = files
        self.by_path = {file.path: file for file in files}
        self.location_cache: dict[Path, ClosureFile | None] = {}
        roots = source_paths if source_paths is not None else frozenset(
            file.relative_path for file in files if file.path.suffix == ".c"
        )
        source_directories = {Path(path).parent for path in roots}
        # Driver-local headers expose entrypoints that need not be referenced by
        # this TU (e.g. allocation wrappers). Shared framework headers are reached
        # through compiler dependencies instead of being blanket roots.
        self.source_paths = roots | frozenset(
            file.relative_path for file in files
            if Path(file.relative_path).parent in source_directories
        )

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> ClosureFileSet:
        source_root = Path(str(manifest.get("source_root", ""))).resolve()
        records: dict[str, str] = {}
        units = manifest.get("translation_units")
        if not isinstance(units, list):
            raise WorkflowError("compile manifest has no translation units")
        for unit in units:
            if not isinstance(unit, dict):
                raise WorkflowError("compile manifest contains an invalid translation unit")
            cls._add_record(records, unit.get("source_path"), unit.get("sha256"))
            dependencies = unit.get("dependencies")
            if not isinstance(dependencies, list):
                raise WorkflowError("compile manifest contains invalid source dependencies")
            for dependency in dependencies:
                if not isinstance(dependency, dict):
                    raise WorkflowError("compile manifest contains an invalid source dependency")
                cls._add_record(records, dependency.get("path"), dependency.get("sha256"))
        files = []
        for relative_path, expected_digest in sorted(records.items()):
            path = (source_root / relative_path).resolve()
            if path != source_root and source_root not in path.parents:
                raise WorkflowError("source-closure path escapes the frozen source root")
            if not path.is_file() or file_sha256(path) != expected_digest:
                raise WorkflowError(f"source-closure file identity changed: {relative_path}")
            files.append(ClosureFile(relative_path, path, expected_digest))
        if not files:
            raise WorkflowError("source closure contains no analyzable C files")
        return cls(source_root, tuple(files), frozenset(
            {unit["source_path"] for unit in units}
            | {file.relative_path for file in files if file.path.suffix == ".c"}
        ))

    @staticmethod
    def _add_record(records: dict[str, str], path: Any, digest: Any) -> None:
        if not isinstance(path, str) or not path or not isinstance(digest, str):
            raise WorkflowError("source-closure file record is incomplete")
        existing = records.setdefault(path, digest)
        if existing != digest:
            raise WorkflowError(f"source-closure file has conflicting identities: {path}")

    def resolve_location(self, value: Any, compile_directory: Path) -> ClosureFile | None:
        if not isinstance(value, str) or not value or value.startswith("<"):
            return None
        location = Path(value)
        path = (location if location.is_absolute() else compile_directory / location).resolve()
        direct = self.by_path.get(path)
        if direct is not None:
            return direct
        if path in self.location_cache:
            return self.location_cache[path]
        match = self._verified_alias(path)
        self.location_cache[path] = match
        return match

    def _verified_alias(self, path: Path) -> ClosureFile | None:
        if not path.is_file():
            return None
        path_parts = path.parts
        candidates = [
            file
            for file in self.files
            if len(path_parts) >= len(Path(file.relative_path).parts)
            and path_parts[-len(Path(file.relative_path).parts) :]
            == Path(file.relative_path).parts
        ]
        if not candidates:
            return None
        digest = file_sha256(path)
        matches = [file for file in candidates if file.sha256 == digest]
        if len(matches) > 1:
            raise WorkflowError(f"Clang source location maps to multiple closure files: {path}")
        return matches[0] if matches else None

    def records(self) -> list[dict[str, str]]:
        return [
            {"path": file.relative_path, "sha256": file.sha256}
            for file in self.files
        ]


class ClosureAstProjector:
    """Stream source roots plus compiler-referenced header declarations."""

    def __init__(self, closure: ClosureFileSet) -> None:
        self.closure = closure

    def project(
        self,
        capture_path: Path,
        output_path: Path,
        *,
        compile_directory: Path,
        capture_sha256: str,
        capture_size: int,
    ) -> dict[str, Any]:
        with capture_path.open("rb") as stream:
            root_kind = next(ijson.items(stream, "kind"), None)
        if root_kind != ClangNodeKind.TRANSLATION_UNIT_DECL:
            raise WorkflowError("Clang AST root is not TranslationUnitDecl")

        tracker = _ClangSourceLocations()
        scope = DeclarationScope()
        owned_ordinals: list[int] = []
        with capture_path.open("rb") as stream:
            for ordinal, node in enumerate(ijson.items(stream, "inner.item")):
                if not isinstance(node, dict):
                    raise WorkflowError("Clang AST contains a non-object top-level node")
                locations = tracker.scan(node)
                if not self._owned(node, locations, compile_directory):
                    continue
                resolved = locations.get(id(node))
                root = resolved is not None and any(
                    owner.relative_path in self.closure.source_paths
                    for candidate in resolved.candidates()
                    if (owner := self.closure.resolve_location(
                        candidate.get("file"), compile_directory
                    )) is not None
                )
                owned_ordinals.append(ordinal)
                scope.add(node, root=root)
        selected_ordinals = {owned_ordinals[index] for index in scope.selected()}
        tracker = _ClangSourceLocations()
        selected: list[dict[str, Any]] = []
        observed_count = 0
        with capture_path.open("rb") as stream:
            for node in ijson.items(stream, "inner.item"):
                observed_count += 1
                if not isinstance(node, dict):
                    raise WorkflowError("Clang AST contains a non-object top-level node")
                locations = tracker.scan(node)
                if observed_count - 1 not in selected_ordinals:
                    continue
                self._annotate(node, locations, compile_directory)
                selected.append(node)
        if not selected:
            raise WorkflowError("typed AST contains no source-closure-owned declarations")

        projection = {
            "kind": ClangNodeKind.TRANSLATION_UNIT_DECL,
            "inner": selected,
            "dpfClosure": {
                "schema_version": 2,
                "selection": "source-and-transitive-compiler-dependencies",
                "source_paths": sorted(self.closure.source_paths),
                "files": self.closure.records(),
                "capture_sha256": capture_sha256,
                "capture_size": capture_size,
                "observed_top_level_nodes": observed_count,
                "selected_top_level_nodes": len(selected),
            },
        }
        output_path.write_text(
            json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        return projection

    def validate(
        self,
        projection: dict[str, Any],
        *,
        compile_directory: Path,
        capture_sha256: Any,
        capture_size: Any,
    ) -> None:
        metadata = projection.get("dpfClosure")
        nodes = projection.get("inner")
        if (
            projection.get("kind") != ClangNodeKind.TRANSLATION_UNIT_DECL
            or not isinstance(metadata, dict)
            or not isinstance(nodes, list)
            or metadata.get("schema_version") != 2
            or metadata.get("selection") != "source-and-transitive-compiler-dependencies"
            or metadata.get("source_paths") != sorted(self.closure.source_paths)
            or metadata.get("files") != self.closure.records()
            or metadata.get("capture_sha256") != capture_sha256
            or metadata.get("capture_size") != capture_size
            or metadata.get("selected_top_level_nodes") != len(nodes)
            or not isinstance(metadata.get("observed_top_level_nodes"), int)
            or metadata["observed_top_level_nodes"] < len(nodes)
        ):
            raise WorkflowError("typed AST closure projection provenance is invalid")
        if any(
            not isinstance(node, dict)
            or not self._owned(
                node,
                _ClangSourceLocations.build(node),
                compile_directory,
            )
            for node in nodes
        ):
            raise WorkflowError("typed AST projection contains a non-closure declaration")

    def _owned(
        self,
        declaration: dict[str, Any],
        locations: dict[int, _ResolvedLocations],
        compile_directory: Path,
    ) -> bool:
        resolved = locations.get(id(declaration))
        if resolved is None:
            return False
        return any(
            self.closure.resolve_location(candidate.get("file"), compile_directory) is not None
            for candidate in resolved.candidates()
        )

    def _annotate(
        self,
        value: Any,
        locations: dict[int, _ResolvedLocations],
        compile_directory: Path,
    ) -> None:
        if isinstance(value, list):
            for item in value:
                self._annotate(item, locations, compile_directory)
            return
        if not isinstance(value, dict):
            return
        children = tuple(value.values())
        location = locations.get(id(value))
        if location is not None:
            value["_dpfSource"] = location.to_record()
            owned_sources = {
                (
                    candidate["kind"],
                    owner.relative_path,
                    owner.sha256,
                    candidate.get("line"),
                    candidate.get("col"),
                )
                for candidate in location.candidates()
                if (owner := self.closure.resolve_location(
                    candidate.get("file"), compile_directory
                ))
                is not None
            }
            if owned_sources:
                value["_dpfClosureSource"] = [
                    {
                        "kind": kind,
                        "path": path,
                        "sha256": digest,
                        "line": line,
                        "col": col,
                    }
                    for kind, path, digest, line, col in sorted(
                        owned_sources,
                        key=lambda item: tuple(
                            "" if part is None else str(part) for part in item
                        ),
                    )
                ]
        for child in children:
            self._annotate(child, locations, compile_directory)
