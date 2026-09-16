from __future__ import annotations

import hashlib
import json
import re
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
from .closure_context import ClosureContextValidator
from .closure_coverage import ClosureCoverageValidator
from .closure_model import ClosureContext, TranslationUnitSet
from .closure_paths import checkout
from .closure_units import TranslationUnitValidator
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


class SourceClosureService:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)

    def validate(self, project: Project, *, closure_path: Path) -> SourceClosureResult:
        project.ensure_role(*self.ROLES)
        self._enter_stage(project)
        controlled_input = self._controlled(project, closure_path)
        input_digest = file_sha256(controlled_input)
        attempt_dir = self._new_attempt_dir(project, input_digest)
        errors: list[str] = []
        details: dict[str, Any] = {}
        compilation_database: list[dict[str, Any]] = []
        compile_manifest: dict[str, Any] = {}
        source_files: dict[str, Path] = {}
        knowledge_revision: SourceCorpusRevision | None = None
        corpus: CorpusManifest | None = None
        added_ids: tuple[str, ...] = ()
        try:
            closure = self._load_json(controlled_input)
            (
                details,
                compilation_database,
                compile_manifest,
                source_files,
            ) = self._validate_closure(project, closure)
            knowledge_revision, corpus, added_ids = self._extend_knowledge(project, source_files)
            if file_sha256(controlled_input) != input_digest:
                raise WorkflowError("source closure submission changed during validation")
        except (WorkflowError, OSError, UnicodeDecodeError, subprocess.SubprocessError) as error:
            errors.append(str(error))
        report_path = self._write_report(
            attempt_dir,
            controlled_input,
            input_digest,
            details,
            errors,
        )
        if errors:
            project.record_artifact(
                SourceAnalysisStage.SOURCE_CLOSURE,
                FileArtifact(
                    SourceAnalysisArtifact.SOURCE_CLOSURE_VALIDATION_ATTEMPT,
                    report_path,
                ),
            )
            return SourceClosureResult(
                ValidationStatus.FAIL,
                StageStatus.RUNNING,
                str(report_path),
                tuple(errors),
            )
        self._finalize(
            project,
            attempt_dir,
            controlled_input,
            report_path,
            compilation_database,
            compile_manifest,
            knowledge_revision,
            corpus,
        )
        if added_ids:
            project.record_event(
                SourceAnalysisEvent.CLOSURE_EXTENDED,
                {"added_ids": list(added_ids), "added_count": len(added_ids)},
            )
        return SourceClosureResult(
            ValidationStatus.PASS,
            StageStatus.PASS,
            str(report_path),
            (),
        )

    def _validate_closure(
        self, project: Project, closure: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Path]]:
        context = ClosureContextValidator().validate(project, closure)
        units = TranslationUnitValidator(context).validate(closure["translation_units"])
        coverage = ClosureCoverageValidator(project, context, units).validate(closure)
        source_files = {**units.source_files, **coverage.source_files}
        manifest = self._compile_manifest(closure, context, units)
        details = {
            "source_revision": context.source.resolved_commit,
            "translation_unit_count": len(units.records),
            "controlled_file_count": len(source_files),
            "closure_categories": coverage.records,
            "compiler_sha256": manifest["compiler"]["sha256"],
        }
        return details, list(units.compilation_database), manifest, source_files

    @staticmethod
    def _compile_manifest(
        closure: dict[str, Any],
        context: ClosureContext,
        units: TranslationUnitSet,
    ) -> dict[str, Any]:
        compiler = {
            **context.compiler,
            "resolved_path": str(context.compiler_path),
            "sha256": file_sha256(context.compiler_path),
            "version_output": context.compiler_version,
            "version_output_sha256": hashlib.sha256(
                context.compiler_version.encode("utf-8")
            ).hexdigest(),
            "verified_target_triple": units.target_triple,
            "verified_target_abi": units.target_abi,
        }
        return {
            "schema_version": 1,
            "source_revision": context.source.resolved_commit,
            "source_root": str(context.source_root),
            "compiler": compiler,
            "defines": list(context.defines),
            "include_paths": [str(path) for path in context.resolved_include_paths],
            "configuration_inputs": closure["configuration_inputs"],
            "generated_headers": closure["generated_headers"],
            "selected_conditional_branches": list(context.conditional_branches),
            "translation_units": list(units.records),
        }

    def _extend_knowledge(
        self, project: Project, source_files: dict[str, Path]
    ) -> tuple[SourceCorpusRevision, CorpusManifest, tuple[str, ...]]:
        base = CorpusManifest.current(project)
        existing_paths = {record.path: record for record in base.records}
        checkouts = load_repository_acquisition(project).checkouts
        source = checkout(checkouts, RepositoryRole.SOURCE)
        source_root = (project.root / source.checkout_path).resolve()
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
                    True,
                    GitBlobOrigin(
                        RepositoryRole.SOURCE,
                        source.resolved_commit,
                        blob,
                        repository_relative,
                    ),
                    category="behaviorally-required-source-closure",
                    notes="Added by the validated source dependency closure.",
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

    def _finalize(
        self,
        project: Project,
        attempt_dir: Path,
        controlled_input: Path,
        report_path: Path,
        compilation_database: list[dict[str, Any]],
        compile_manifest: dict[str, Any],
        knowledge_revision: SourceCorpusRevision | None,
        corpus: CorpusManifest | None,
    ) -> None:
        if corpus is None or knowledge_revision is None:
            raise WorkflowError("validated source closure has no corpus revision")
        compilation_path = attempt_dir / "compile_commands.json"
        compilation_bytes = self._json_array_bytes(compilation_database)
        compilation_path.write_bytes(compilation_bytes)
        compile_manifest["compilation_database_sha256"] = hashlib.sha256(
            compilation_bytes
        ).hexdigest()
        compile_manifest_path = attempt_dir / "compile-manifest.json"
        compile_manifest_path.write_bytes(self._json_bytes(compile_manifest))
        project.finalize_stage(
            SourceAnalysisStage.SOURCE_CLOSURE,
            (
                FileArtifact(SourceAnalysisArtifact.SOURCE_CLOSURE, controlled_input),
                FileArtifact(SourceAnalysisArtifact.SOURCE_CLOSURE_REPORT, report_path),
                FileArtifact(SourceAnalysisArtifact.COMPILE_MANIFEST, compile_manifest_path),
                FileArtifact(SourceAnalysisArtifact.COMPILATION_DATABASE, compilation_path),
                GeneratedArtifact(
                    SourceAnalysisArtifact.MATERIALS_MANIFEST,
                    corpus.data,
                    corpus.source,
                ),
                GeneratedArtifact(
                    SourceAnalysisArtifact.KNOWLEDGE_REVISION,
                    self._json_bytes(knowledge_revision.to_dict()),
                    f"generated:source-closure:{corpus.digest}",
                ),
            ),
        )

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
    def _write_report(
        attempt_dir: Path,
        controlled_input: Path,
        input_digest: str,
        details: dict[str, Any],
        errors: list[str],
    ) -> Path:
        report = {
            "schema_version": 1,
            "status": ValidationStatus.PASS if not errors else ValidationStatus.FAIL,
            "closure_input": {"path": str(controlled_input), "sha256": input_digest},
            "details": details,
            "errors": errors,
            "validated_at": utc_now(),
        }
        report_path = attempt_dir / "validation.json"
        report_path.write_bytes(SourceClosureService._json_bytes(report))
        return report_path

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
    def _controlled(project: Project, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != project.root and project.root not in resolved.parents:
            raise WorkflowError("source closure submission must remain inside the project")
        if not resolved.is_file():
            raise WorkflowError(f"source closure submission is not a file: {resolved}")
        return resolved

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise WorkflowError(f"invalid source closure JSON: {path}") from error
        if not isinstance(value, dict):
            raise WorkflowError("source closure must be a JSON object")
        return value

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
