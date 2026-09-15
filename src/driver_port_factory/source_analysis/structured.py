from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.models import ActorRole, FileArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from .bundle_extraction import StructuredBundleExtractor
from .clang_backend import AnalyzerFamily
from .contracts import SourceAnalysisArtifact, SourceAnalysisStage
from .fact_model import StructuredAnalysisStatus


@dataclass(frozen=True, slots=True)
class StructuredAnalysisResult:
    status: StructuredAnalysisStatus
    stage_status: StageStatus
    report_path: str
    errors: tuple[str, ...]


class StructuredCAnalysisService:
    """Orchestrate the Skill's structured-C evidence gate without interpreting C text."""

    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)

    def analyze(
        self,
        project: Project,
        *,
        analyzer: str = "clang",
        analyzer_family: AnalyzerFamily = AnalyzerFamily.CLANG_LLVM,
    ) -> StructuredAnalysisResult:
        project.ensure_role(*self.ROLES)
        self._enter_stage(project)
        compile_ref = project.artifact(
            SourceAnalysisStage.SOURCE_CLOSURE,
            SourceAnalysisArtifact.COMPILE_MANIFEST,
        )
        database_ref = project.artifact(
            SourceAnalysisStage.SOURCE_CLOSURE,
            SourceAnalysisArtifact.COMPILATION_DATABASE,
        )
        compile_bytes = project.artifacts.read(compile_ref)
        database_bytes = project.artifacts.read(database_ref)
        input_digest = hashlib.sha256(compile_bytes + database_bytes).hexdigest()
        attempt_dir = self._new_attempt_dir(project, input_digest)
        report_path = attempt_dir / "report.json"
        errors: list[str] = []
        facts: dict[str, Any] = {}
        outputs: tuple[tuple[SourceAnalysisArtifact, Path], ...] = ()

        try:
            compile_manifest = self._json_object(compile_bytes, "compile_manifest")
            compilation_database = self._json_array(database_bytes, "compilation_database")
            facts, artifact_paths = StructuredBundleExtractor().extract(
                project=project,
                attempt_dir=attempt_dir,
                compile_manifest=compile_manifest,
                compilation_database=compilation_database,
                compile_manifest_digest=compile_ref.digest,
                compilation_database_digest=database_ref.digest,
                analyzer_name=analyzer,
                analyzer_family=analyzer_family,
            )
            facts_path = attempt_dir / "structured-c-facts.json"
            facts_path.write_bytes(self._json_bytes(facts))
            outputs = (
                (SourceAnalysisArtifact.STRUCTURED_C_FACTS, facts_path),
                (SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_REPORT, report_path),
                *artifact_paths,
            )
        except (OSError, ValueError, WorkflowError) as error:
            errors.append(str(error))

        report = {
            "schema_version": 1,
            "status": (
                StructuredAnalysisStatus.READY if not errors else StructuredAnalysisStatus.FAIL
            ),
            "input_sha256": input_digest,
            "attempt_path": str(attempt_dir.relative_to(project.root)),
            "unit_count": len(facts.get("units", [])),
            "errors": errors,
            "completed_at": utc_now(),
        }
        report_path.write_bytes(self._json_bytes(report))
        if errors:
            project.record_artifact(
                SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
                FileArtifact(
                    SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_ATTEMPT,
                    report_path,
                ),
            )
            return StructuredAnalysisResult(
                StructuredAnalysisStatus.FAIL,
                StageStatus.RUNNING,
                str(report_path),
                tuple(errors),
            )

        project.finalize_stage(
            SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
            tuple(FileArtifact(kind, path) for kind, path in outputs),
        )
        return StructuredAnalysisResult(
            StructuredAnalysisStatus.READY,
            StageStatus.PASS,
            str(report_path),
            (),
        )

    @staticmethod
    def _enter_stage(project: Project) -> None:
        stage = project.stage(SourceAnalysisStage.STRUCTURED_C_ANALYSIS)
        if stage.status is StageStatus.READY:
            project.start(SourceAnalysisStage.STRUCTURED_C_ANALYSIS)
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(
                f"structured_c_analysis must be READY or RUNNING, got {stage.status.value}"
            )

    @staticmethod
    def _new_attempt_dir(project: Project, input_digest: str) -> Path:
        root = project.control / "structured-c" / "attempts"
        root.mkdir(parents=True, exist_ok=True)
        for sequence in range(1, 10000):
            candidate = root / f"{input_digest[:16]}-{sequence:04d}"
            try:
                candidate.mkdir()
            except FileExistsError:
                continue
            return candidate
        raise WorkflowError("structured C analysis attempt sequence is exhausted")

    @staticmethod
    def _json_object(data: bytes, label: str) -> dict[str, Any]:
        try:
            value = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError(f"{label} is not UTF-8 JSON") from error
        if not isinstance(value, dict):
            raise WorkflowError(f"{label} is not a JSON object")
        return value

    @staticmethod
    def _json_array(data: bytes, label: str) -> list[dict[str, Any]]:
        try:
            value = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError(f"{label} is not UTF-8 JSON") from error
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise WorkflowError(f"{label} is not an array of objects")
        return value

    @staticmethod
    def _json_bytes(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
