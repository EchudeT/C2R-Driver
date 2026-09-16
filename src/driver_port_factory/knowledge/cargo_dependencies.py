from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from ..acquisition.facets import (
    EvidenceFacet,
    EvidenceLane,
    MaterialRedistribution,
    TargetFacet,
)
from ..acquisition.frozen_checkout_validation import verify_git_checkout
from ..acquisition.material import CargoRegistryOrigin, MaterialRecord
from ..acquisition.material_content import MaterialContentPolicy
from ..acquisition.parsing import sha256
from ..acquisition.repository import load_repository_acquisition
from ..acquisition.repository_checkout import CheckoutRecord
from ..acquisition.repository_role import RepositoryRole
from ..core.execution import CommandResult, CommandRunner
from ..core.models import WorkflowError, utc_now
from ..core.project import Project
from .corpus import CorpusManifest
from .index import file_sha256

_REGISTRY_SOURCE_PREFIX = "registry+"
_SEARCHABLE_SUFFIXES = frozenset({".rs", ".toml", ".md", ".txt", ".rst"})
_TARGET_FACET = EvidenceFacet(EvidenceLane.TARGET, TargetFacet.API_DEFINITIONS_AND_CALLS)


class TargetDependencySystem(StrEnum):
    NONE = "none"
    CARGO = "cargo"


@dataclass(frozen=True, slots=True)
class CargoPackage:
    package_id: str
    name: str
    version: str
    registry_url: str
    checksum: str
    manifest_path: Path
    license_note: str

    @classmethod
    def from_metadata(cls, value: dict[str, object]) -> CargoPackage:
        source = _text(value, "source")
        if not source.startswith(_REGISTRY_SOURCE_PREFIX):
            raise WorkflowError("resolved Cargo package is not registry-backed")
        license_note = value.get("license")
        return cls(
            _text(value, "id"),
            _text(value, "name"),
            _text(value, "version"),
            source.removeprefix(_REGISTRY_SOURCE_PREFIX),
            sha256(value.get("checksum"), "Cargo registry package checksum"),
            Path(_text(value, "manifest_path")).resolve(),
            license_note if isinstance(license_note, str) and license_note else "review-required",
        )


@dataclass(frozen=True, slots=True)
class CargoResolution:
    graph: dict[str, object]
    registry_packages: tuple[CargoPackage, ...]

    @classmethod
    def from_bytes(cls, data: bytes) -> CargoResolution:
        try:
            document = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("cargo metadata did not return valid JSON") from error
        if not isinstance(document, dict) or not isinstance(document.get("resolve"), dict):
            raise WorkflowError("cargo metadata lacks a resolve graph")
        graph = document["resolve"]
        nodes = graph.get("nodes")
        packages = document.get("packages")
        if not isinstance(nodes, list) or not isinstance(packages, list):
            raise WorkflowError("cargo metadata lacks resolved package records")
        resolved = {
            node.get("id")
            for node in nodes
            if isinstance(node, dict) and isinstance(node.get("id"), str)
        }
        registry_packages = tuple(
            CargoPackage.from_metadata(package)
            for package in packages
            if isinstance(package, dict)
            and package.get("id") in resolved
            and str(package.get("source", "")).startswith(_REGISTRY_SOURCE_PREFIX)
        )
        return cls(graph, registry_packages)


class CargoDependencyClosure:
    """Add the registry source closure resolved from the frozen target Cargo graph."""

    def extend(self, project: Project, manifest: CorpusManifest) -> CorpusManifest:
        acquisition = load_repository_acquisition(project)
        target = acquisition.checkout(RepositoryRole.TARGET)
        verify_git_checkout(project.root, target)
        target_root = (project.root / target.checkout_path).resolve()
        cargo_manifest = target_root / "Cargo.toml"
        if self._dependency_system(cargo_manifest) is TargetDependencySystem.NONE:
            return manifest
        if not self._active_project(cargo_manifest):
            return manifest

        attempt, export, cargo_home, runner = self._resolution_workspace(project, target)
        self._export_target(project, target.bare_repository, target.resolved_commit, export, runner)
        environment = {
            "CARGO_HOME": str(cargo_home),
            "CARGO_TARGET_DIR": str(attempt / "cargo-target"),
            "CARGO_TERM_COLOR": "never",
        }
        identity = self._run(runner, ("cargo", "--version", "--verbose"), export, environment)
        metadata_bytes = self._run(
            runner,
            ("cargo", "metadata", "--format-version", "1", "--all-features"),
            export,
            environment,
        )
        (attempt / "cargo-identity.txt").write_bytes(identity)
        (attempt / "metadata.json").write_bytes(metadata_bytes)
        lock = export / "Cargo.lock"
        if not lock.is_file():
            raise WorkflowError("cargo metadata did not preserve a Cargo.lock resolution")
        resolution = CargoResolution.from_bytes(metadata_bytes)
        (attempt / "resolve.json").write_text(
            json.dumps(resolution.graph, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        records = tuple(
            record
            for package in resolution.registry_packages
            for record in self._materialize_package(project, cargo_home, package)
        )
        if not records:
            return manifest
        return CorpusManifest.candidate(
            (*manifest.records, *records),
            parent_digest=manifest.digest,
        )

    @staticmethod
    def _dependency_system(manifest: Path) -> TargetDependencySystem:
        return TargetDependencySystem.CARGO if manifest.is_file() else TargetDependencySystem.NONE

    @staticmethod
    def _active_project(manifest: Path) -> bool:
        try:
            document = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
            raise WorkflowError(f"target Cargo manifest is unreadable: {error}") from error
        workspace = document.get("workspace")
        members = workspace.get("members") if isinstance(workspace, dict) else None
        return isinstance(document.get("package"), dict) or bool(members)

    @staticmethod
    def _resolution_workspace(
        project: Project, target: CheckoutRecord
    ) -> tuple[Path, Path, Path, CommandRunner]:
        commit = target.resolved_commit
        root = project.control / "knowledge" / "cargo" / commit
        root.mkdir(parents=True, exist_ok=True)
        attempt = Path(tempfile.mkdtemp(prefix="attempt-", dir=root))
        export = attempt / "target"
        export.mkdir()
        cargo_home = attempt / "cargo-home"
        cargo_home.mkdir()
        runner = CommandRunner(project.control / "command-runs" / "knowledge-cargo" / commit)
        return attempt, export, cargo_home, runner

    @staticmethod
    def _export_target(
        project: Project,
        bare_repository: str,
        commit: str,
        destination: Path,
        runner: CommandRunner,
    ) -> None:
        bare = (project.root / bare_repository).resolve()
        result = runner.run(
            ("git", "-C", str(bare), "archive", "--format=tar", commit),
            cwd=project.root,
        )
        archive_path = Path(result.stdout_path)
        if result.exit_code != 0:
            raise WorkflowError(_command_error(result, "frozen target export"))
        _extract_target_archive(archive_path, destination)

    @staticmethod
    def _run(
        runner: CommandRunner,
        argv: tuple[str, ...],
        cwd: Path,
        environment: dict[str, str],
    ) -> bytes:
        result = runner.run(argv, cwd=cwd, environment=environment, timeout_seconds=600)
        if result.exit_code != 0:
            raise WorkflowError(_command_error(result, "Cargo dependency resolution"))
        return Path(result.stdout_path).read_bytes()

    @staticmethod
    def _materialize_package(
        project: Project,
        cargo_home: Path,
        package: CargoPackage,
    ) -> tuple[MaterialRecord, ...]:
        package_root = package.manifest_path.parent
        if cargo_home.resolve() not in package_root.parents or not package.manifest_path.is_file():
            raise WorkflowError("Cargo registry source is outside the project-local CARGO_HOME")
        archive = next(
            (
                path
                for path in cargo_home.glob(
                    f"registry/cache/*/{package.name}-{package.version}.crate"
                )
                if file_sha256(path) == package.checksum
            ),
            None,
        )
        if archive is None:
            raise WorkflowError(
                "Cargo registry archive is unavailable or invalid: "
                f"{package.name}@{package.version}"
            )
        key = hashlib.sha256(package.package_id.encode("utf-8")).hexdigest()[:20]
        root = project.root / "knowledge" / "raw" / "target-cargo" / key
        archive_path = root / f"{package.name}-{package.version}.crate"
        archive_bytes = archive.read_bytes()
        _publish(archive_path, archive_bytes)
        controlled_archive = str(archive_path.relative_to(project.root))
        acquired_at = utc_now()

        def origin(relative: str | None) -> CargoRegistryOrigin:
            return CargoRegistryOrigin(
                package.registry_url,
                package.name,
                package.version,
                package.checksum,
                controlled_archive,
                hashlib.sha256(archive_bytes).hexdigest(),
                relative,
            )

        records = [
            MaterialRecord(
                f"target.cargo.{key}.archive",
                _TARGET_FACET,
                controlled_archive,
                package.registry_url,
                package.version,
                acquired_at,
                package.license_note,
                MaterialRedistribution.UNKNOWN,
                package.checksum,
                len(archive_bytes),
                "application/octet-stream",
                True,
                False,
                origin(None),
            )
        ]
        for source in sorted(package_root.rglob("*")):
            if not source.is_file() or source.suffix.lower() not in _SEARCHABLE_SUFFIXES:
                continue
            resolved = source.resolve()
            if package_root not in resolved.parents:
                raise WorkflowError("Cargo registry source path escapes its package root")
            data = resolved.read_bytes()
            if not data:
                continue
            relative = source.relative_to(package_root).as_posix()
            destination = root / "source" / relative
            _publish(destination, data)
            media_type = MaterialContentPolicy.media_type(source)
            identifier = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:20]
            records.append(
                MaterialRecord(
                    f"target.cargo.{key}.source.{identifier}",
                    _TARGET_FACET,
                    str(destination.relative_to(project.root)),
                    package.registry_url,
                    package.version,
                    acquired_at,
                    package.license_note,
                    MaterialRedistribution.UNKNOWN,
                    hashlib.sha256(data).hexdigest(),
                    len(data),
                    media_type,
                    True,
                    True,
                    origin(relative),
                )
            )
        return tuple(records)


def _extract_target_archive(archive_path: Path, destination: Path) -> None:
    root = destination.resolve()
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            for member in archive.getmembers():
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise WorkflowError("frozen target archive contains an unsafe path")
                output = destination.joinpath(*relative.parts)
                parent = output.parent.resolve()
                if parent != root and root not in parent.parents:
                    raise WorkflowError("frozen target archive path escapes through a symlink")
                if member.isdir():
                    output.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    source = archive.extractfile(member)
                    if source is None:
                        raise WorkflowError("frozen target archive contains an unreadable file")
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(source.read())
                elif member.issym():
                    _extract_symlink(root, output, member.linkname)
                else:
                    raise WorkflowError("frozen target archive contains a dangerous entry")
    except (OSError, tarfile.TarError) as error:
        raise WorkflowError(f"frozen target archive export failed: {error}") from error


def _extract_symlink(root: Path, output: Path, linkname: str) -> None:
    target = PurePosixPath(linkname)
    if not linkname or target.is_absolute():
        raise WorkflowError("frozen target archive symlink is unsafe")
    try:
        resolved = output.parent.joinpath(*target.parts).resolve()
    except RuntimeError as error:
        raise WorkflowError("frozen target archive symlink cannot be resolved safely") from error
    if resolved != root and root not in resolved.parents:
        raise WorkflowError("frozen target archive symlink escapes its export root")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.symlink_to(linkname)


def _text(value: dict[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise WorkflowError(f"Cargo package lacks {field}")
    return item


def _command_error(result: CommandResult, purpose: str) -> str:
    stderr = Path(result.stderr_path).read_text(encoding="utf-8", errors="replace").strip()
    detail = stderr or result.launch_error or "no diagnostic"
    return f"{purpose} failed ({result.exit_code}): {detail}"


def _publish(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise WorkflowError(f"controlled Cargo material path is a symlink: {path}")
    if path.exists() and path.read_bytes() != data:
        raise WorkflowError(f"controlled Cargo material changed at {path}")
    if not path.exists():
        path.write_bytes(data)
