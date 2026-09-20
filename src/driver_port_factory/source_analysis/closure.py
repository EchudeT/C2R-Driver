from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..acquisition.facets import EvidenceFacet, EvidenceLane, MaterialRedistribution, SourceFacet
from ..acquisition.material import GitBlobOrigin, MaterialRecord
from ..acquisition.repository import load_repository_acquisition
from ..acquisition.repository_role import RepositoryRole
from ..core.models import (
    ActorRole,
    FileArtifact,
    GeneratedArtifact,
    StageStatus,
    WorkflowError,
    utc_now,
)
from ..core.project import Project
from ..knowledge.corpus import CorpusManifest
from ..knowledge.index import KnowledgeIndex, file_sha256
from .closure_paths import checkout
from .compiler import CompilerFamily, compiler_adapter
from .contracts import (
    SourceAnalysisArtifact,
    SourceAnalysisEvent,
    SourceAnalysisStage,
    ValidationStatus,
)
from .corpus_revision import SourceCorpusRevision


@dataclass(frozen=True, slots=True)
class SourceClosureResult:
    status: ValidationStatus
    stage_status: StageStatus
    report_path: str
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NormalizedCompileCommand:
    directory: Path
    source_path: Path
    compiler_path: Path
    arguments: list[str]


def _normalize_compile_command(
    source_root: Path, raw: object, workspace_root: Path | None = None
) -> NormalizedCompileCommand:
    if not isinstance(raw, dict):
        raise WorkflowError("compile command must be an object")
    directory = _inside_source_root(
        workspace_root or source_root,
        Path(str(raw.get("directory", source_root))),
        source_root,
        "compile command directory",
    )
    source_path = _inside_source_root(
        source_root,
        Path(str(raw.get("file", ""))),
        directory,
        "compile command source",
    )
    if not source_path.is_file():
        raise WorkflowError("compile command source is not a file")
    arguments = raw.get("arguments")
    if arguments is None and isinstance(raw.get("command"), str):
        arguments = shlex.split(raw["command"])
    if not isinstance(arguments, list) or not arguments or not all(
        isinstance(argument, str) and argument for argument in arguments
    ):
        raise WorkflowError("compile command requires an argv list or command string")
    compiler = _resolve_executable(arguments[0], directory)
    if not compiler.is_file():
        raise WorkflowError(f"source compiler is unavailable: {arguments[0]}")
    return NormalizedCompileCommand(
        directory,
        source_path,
        compiler,
        [str(compiler), *arguments[1:]],
    )


def _inside_source_root(
    source_root: Path,
    value: Path,
    relative_to: Path,
    label: str,
) -> Path:
    resolved = (value if value.is_absolute() else relative_to / value).resolve()
    if resolved != source_root and source_root not in resolved.parents:
        raise WorkflowError(f"{label} is outside the frozen source tree")
    return resolved


def _resolve_executable(value: str, cwd: Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.absolute()
    if "/" in value:
        return (cwd / candidate).absolute()
    located = shutil.which(value)
    # Preserve argv[0]: compiler-cache and multicall symlinks dispatch by basename.
    return Path(located).absolute() if located else Path()


class SourceClosureService:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)

    def prepare_compilation_database(
        self,
        project: Project,
        *,
        compilation_database_path: Path,
        work_report_path: Path,
    ) -> SourceClosureResult:
        """Derive mechanical closure records from standard ``compile_commands.json``."""

        project.ensure_role(*self.ROLES)
        self._enter_stage(project)
        try:
            raw_entries = json.loads(compilation_database_path.read_text(encoding="utf-8"))
            if not isinstance(raw_entries, list) or not raw_entries:
                raise WorkflowError("compile_commands.json must contain at least one command")
            acquisition = load_repository_acquisition(project)
            source = checkout(acquisition.checkouts, RepositoryRole.SOURCE)
            source_root = (project.root / source.checkout_path).resolve()
            database, units, source_files, compiler = self._derive_compile_inputs(
                source_root, raw_entries, workspace_root=project.root
            )
            corpus_revision, corpus, added_ids = self._extend_knowledge(
                project, source_files, source_paths={unit["source_path"] for unit in units}
            )
            attempt_dir = self._new_attempt_dir(
                project,
                hashlib.sha256(compilation_database_path.read_bytes()).hexdigest(),
            )
            database_path = attempt_dir / "compile_commands.json"
            database_bytes = self._json_array_bytes(database)
            database_path.write_bytes(database_bytes)
            compile_manifest = {
                "schema_version": 1,
                "source_revision": source.resolved_commit,
                "source_root": str(source_root),
                "compiler": compiler,
                "translation_units": units,
                "compilation_database_sha256": hashlib.sha256(database_bytes).hexdigest(),
            }
            compile_manifest_path = attempt_dir / "compile-manifest.json"
            compile_manifest_path.write_bytes(self._json_bytes(compile_manifest))
            report_path = attempt_dir / "validation.json"
            report_path.write_bytes(
                self._json_bytes(
                    {
                        "schema_version": 1,
                        "status": ValidationStatus.PASS,
                        "work_report": {
                            "path": str(work_report_path.relative_to(project.root)),
                            "sha256": file_sha256(work_report_path),
                        },
                        "translation_unit_count": len(units),
                        "controlled_file_count": len(source_files),
                        "errors": [],
                        "validated_at": utc_now(),
                    }
                )
            )
            from .preparation import save
            save(
                project,
                (
                    FileArtifact(SourceAnalysisArtifact.SOURCE_CLOSURE, work_report_path),
                    FileArtifact(SourceAnalysisArtifact.SOURCE_CLOSURE_REPORT, report_path),
                    FileArtifact(SourceAnalysisArtifact.COMPILE_MANIFEST, compile_manifest_path),
                    FileArtifact(SourceAnalysisArtifact.COMPILATION_DATABASE, database_path),
                    GeneratedArtifact(
                        SourceAnalysisArtifact.MATERIALS_MANIFEST,
                        corpus.data,
                        corpus.source,
                    ),
                    GeneratedArtifact(
                        SourceAnalysisArtifact.KNOWLEDGE_REVISION,
                        self._json_bytes(corpus_revision.to_dict()),
                        f"generated:source-closure:{corpus.digest}",
                    ),
                ),
                request_sha256=hashlib.sha256(compilation_database_path.read_bytes()).hexdigest(),
            )
            if added_ids:
                project.record_event(
                    SourceAnalysisEvent.CLOSURE_EXTENDED,
                    {"added_ids": list(added_ids), "added_count": len(added_ids)},
                )
            return SourceClosureResult(
                ValidationStatus.PASS, StageStatus.RUNNING, str(report_path), ()
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, WorkflowError) as error:
            raise WorkflowError(f"source closure mechanical processing failed: {error}") from error

    @staticmethod
    def _derive_compile_inputs(
        source_root: Path, raw_entries: list[Any], *, workspace_root: Path | None = None
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Path],
        dict[str, Any],
    ]:
        adapter = compiler_adapter(CompilerFamily.GCC_COMPATIBLE)
        database: list[dict[str, Any]] = []
        units: list[dict[str, Any]] = []
        source_files: dict[str, Path] = {}
        compiler_path: Path | None = None
        target_triple: str | None = None
        target_abi: dict[str, object] | None = None
        for index, raw in enumerate(raw_entries, start=1):
            entry = _normalize_compile_command(source_root, raw, workspace_root)
            directory = entry.directory
            source_path = entry.source_path
            resolved_compiler = entry.compiler_path
            if compiler_path is None:
                compiler_path = resolved_compiler
            elif compiler_path != resolved_compiler:
                raise WorkflowError("compile commands must use one compiler executable")
            normalized_arguments = entry.arguments
            if not adapter.contains_source(normalized_arguments, directory, source_path):
                raise WorkflowError("compile argv does not name its source file")
            observed_triple = adapter.effective_target_triple(
                normalized_arguments, directory, executable=resolved_compiler
            )
            observed_abi = adapter.abi_signature(
                normalized_arguments, directory, executable=resolved_compiler
            )
            if target_triple is None:
                target_triple, target_abi = observed_triple, observed_abi
            elif target_triple != observed_triple or target_abi != observed_abi:
                raise WorkflowError("compile commands do not share one target ABI")
            dependencies = adapter.dependencies(
                normalized_arguments, directory, workspace_root or source_root, source_path
            )
            generated = dependencies - {p for p in dependencies if source_root in p.parents}
            dependencies -= generated
            relative_source = source_path.relative_to(source_root).as_posix()
            dependency_records = [
                {
                    "path": dependency.relative_to(source_root).as_posix(),
                    "sha256": file_sha256(dependency),
                    "role": "compiler-discovered",
                }
                for dependency in sorted(dependencies)
            ]
            unit_id = f"tu-{index:03d}-{hashlib.sha256(relative_source.encode()).hexdigest()[:12]}"
            units.append(
                {
                    "unit_id": unit_id,
                    "source_path": relative_source,
                    "sha256": file_sha256(source_path),
                    "compile_directory": str(directory),
                    "arguments": normalized_arguments,
                    "dependencies": dependency_records,
                    "generated_dependencies": [
                        {
                            "path": str(path.relative_to(workspace_root or source_root)),
                            "sha256": file_sha256(path),
                        }
                        for path in sorted(generated)
                    ],
                }
            )
            database.append(
                {
                    "directory": str(directory),
                    "file": str(source_path),
                    "arguments": normalized_arguments,
                }
            )
            source_files[relative_source] = source_path
            source_files.update(
                {
                    dependency.relative_to(source_root).as_posix(): dependency
                    for dependency in dependencies
                }
            )
        assert compiler_path is not None and target_triple is not None and target_abi is not None
        version = adapter.version(compiler_path)
        compiler = {
            "family": CompilerFamily.GCC_COMPATIBLE,
            "resolved_path": str(compiler_path),
            "sha256": file_sha256(compiler_path),
            "version_output": version,
            "version_output_sha256": hashlib.sha256(version.encode()).hexdigest(),
            "verified_target_triple": target_triple,
            "verified_target_abi": target_abi,
        }
        return database, units, source_files, compiler

    def _extend_knowledge(
        self, project: Project, source_files: dict[str, Path], *, source_paths: set[str] | None = None
    ) -> tuple[SourceCorpusRevision, CorpusManifest, tuple[str, ...]]:
        from ..knowledge.contracts import KnowledgeArtifact, KnowledgeStage
        from ..acquisition.material import parse_materials
        status = project.load_json_artifact(KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.STATUS)
        parent_path = project.root / status["manifest_path"]
        parent_data = parent_path.read_bytes()
        if hashlib.sha256(parent_data).hexdigest() != status["manifest_sha256"]:
            raise WorkflowError("source preparation parent corpus is damaged")
        base = CorpusManifest(parse_materials(parent_data), parent_data,
                              status["manifest_sha256"], str(parent_path))
        existing_paths = {record.path: record for record in base.records}
        checkouts = load_repository_acquisition(project).checkouts
        source = checkout(checkouts, RepositoryRole.SOURCE)
        source_root = (project.root / source.checkout_path).resolve()
        roots = source_paths if source_paths is not None else {
            relative for relative in source_files if Path(relative).suffix == ".c"
        }
        local_directories = {Path(relative).parent for relative in roots}
        additions: list[MaterialRecord] = []
        for relative, path in sorted(source_files.items()):
            workspace_relative = str(path.relative_to(project.root))
            if workspace_relative in existing_paths:
                if existing_paths[workspace_relative].sha256 != file_sha256(path):
                    raise WorkflowError(f"existing knowledge hash differs for {relative}")
                continue
            identifier = "source-closure-" + re.sub(r"[^A-Za-z0-9._-]+", "-", relative).strip("-")
            if any(record.identifier == identifier for record in (*base.records, *additions)):
                identifier += "-" + hashlib.sha256(relative.encode()).hexdigest()[:8]
            repository_relative = path.relative_to(source_root).as_posix()
            blob = self._git_blob(source_root, source.resolved_commit, repository_relative)
            additions.append(
                MaterialRecord(
                    identifier,
                    EvidenceFacet(EvidenceLane.SOURCE, SourceFacet.DEPENDENCY_CLOSURE),
                    workspace_relative,
                    source.source_url,
                    source.resolved_commit,
                    utc_now(),
                    "review-required",
                    MaterialRedistribution.UNKNOWN,
                    file_sha256(path),
                    path.stat().st_size,
                    "text/plain",
                    True,
                    relative in roots or Path(relative).parent in local_directories,
                    GitBlobOrigin(
                        RepositoryRole.SOURCE,
                        source.resolved_commit,
                        blob,
                        repository_relative,
                    ),
                    category="compiler-dependency-closure",
                    notes=("Frozen compiler dependency; shared headers are available as originals "
                           "and reachable c-facts, not blanket full-text search chunks."),
                )
            )
        corpus = CorpusManifest.candidate((*base.records, *additions), parent_digest=base.digest)
        knowledge = KnowledgeIndex(project.root, corpus)
        index_status = knowledge.build()
        revision = SourceCorpusRevision(
            base.digest,
            tuple(additions),
            corpus.digest,
            index_status,
        )
        return revision, corpus, tuple(record.identifier for record in additions)

    @staticmethod
    def _git_blob(root: Path, revision: str, relative: str) -> str:
        completed = subprocess.run(
            ("git", "-C", str(root), "rev-parse", f"{revision}:{relative}"),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise WorkflowError(
                f"source closure file is not tracked at the frozen revision: {relative}"
            )
        return completed.stdout.strip()

    @staticmethod
    def _enter_stage(project: Project) -> None:
        stage = project.stage(SourceAnalysisStage.SOURCE_CLOSURE)
        if stage.status is StageStatus.READY:
            project.start(SourceAnalysisStage.SOURCE_CLOSURE)
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(
                f"source_closure must be READY or RUNNING, got {stage.status.value}"
            )

    @staticmethod
    def _new_attempt_dir(project: Project, input_digest: str) -> Path:
        root = project.control / "source-closure" / "attempts"
        root.mkdir(parents=True, exist_ok=True)
        prefix = input_digest[:16]
        for sequence in range(1, 10000):
            candidate = root / f"{prefix}-{sequence:04d}"
            try:
                candidate.mkdir()
            except FileExistsError:
                continue
            return candidate
        raise WorkflowError("source closure attempt sequence is exhausted")

    @staticmethod
    def _json_bytes(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )

    @staticmethod
    def _json_array_bytes(value: list[dict[str, Any]]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
