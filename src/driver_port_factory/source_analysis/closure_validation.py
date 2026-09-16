from __future__ import annotations

from types import MappingProxyType

from ..acquisition.material import parse_materials
from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, json_object, json_value
from .contracts import SourceAnalysisArtifact, SourceClosureStatus, ValidationStatus
from .corpus_revision import SourceCorpusRevision


def _source_closure(data: bytes) -> None:
    value = json_object(data, SourceAnalysisArtifact.SOURCE_CLOSURE.value)
    if value.get("schema_version") != 1:
        raise WorkflowError("source_closure must be schema_version=1")
    if SourceClosureStatus(value.get("closure_status")) is not SourceClosureStatus.CLOSED:
        raise WorkflowError("source_closure must be CLOSED")
    if not isinstance(value.get("compiler"), dict):
        raise WorkflowError("source_closure requires a compiler identity")
    if not isinstance(value.get("translation_units"), list) or not value["translation_units"]:
        raise WorkflowError("source_closure requires translation units")
    if value.get("unresolved_dependencies") != []:
        raise WorkflowError("source_closure cannot contain unresolved dependencies")


def _closure_report(data: bytes) -> None:
    value = json_object(data, SourceAnalysisArtifact.SOURCE_CLOSURE_REPORT.value)
    if ValidationStatus(value.get("status")) is not ValidationStatus.PASS or value.get("errors"):
        raise WorkflowError("source_closure_report must pass without errors")


def _closure_attempt(data: bytes) -> None:
    value = json_object(data, SourceAnalysisArtifact.SOURCE_CLOSURE_VALIDATION_ATTEMPT.value)
    if ValidationStatus(value.get("status")) is not ValidationStatus.FAIL:
        raise WorkflowError("source_closure_validation_attempt must record FAIL")
    if not isinstance(value.get("errors"), list) or not value["errors"]:
        raise WorkflowError("source_closure_validation_attempt must preserve errors")


def _compile_manifest(data: bytes) -> None:
    value = json_object(data, SourceAnalysisArtifact.COMPILE_MANIFEST.value)
    compiler = value.get("compiler")
    units = value.get("translation_units")
    if value.get("schema_version") != 1 or not value.get("source_revision"):
        raise WorkflowError("compile_manifest requires its source revision")
    if not isinstance(compiler, dict) or len(str(compiler.get("sha256", ""))) != 64:
        raise WorkflowError("compile_manifest requires a hashed compiler")
    if not isinstance(units, list) or not units:
        raise WorkflowError("compile_manifest requires translation units")


def _compilation_database(data: bytes) -> None:
    value = json_value(data, SourceAnalysisArtifact.COMPILATION_DATABASE.value)
    if not isinstance(value, list) or not value:
        raise WorkflowError("compilation_database must be a non-empty JSON array")
    for entry in value:
        if not isinstance(entry, dict) or not all(
            entry.get(field) for field in ("directory", "file", "arguments")
        ):
            raise WorkflowError("compilation_database entries require directory, file, argv")
        if not isinstance(entry["arguments"], list):
            raise WorkflowError("compilation_database arguments must be an argv list")


def _materials_manifest(data: bytes) -> None:
    parse_materials(data)


def _knowledge_revision(data: bytes) -> None:
    SourceCorpusRevision.from_dict(
        json_object(data, SourceAnalysisArtifact.KNOWLEDGE_REVISION.value)
    )


VALIDATORS = MappingProxyType[SourceAnalysisArtifact, ArtifactValidator](
    {
        SourceAnalysisArtifact.SOURCE_CLOSURE: _source_closure,
        SourceAnalysisArtifact.SOURCE_CLOSURE_REPORT: _closure_report,
        SourceAnalysisArtifact.SOURCE_CLOSURE_VALIDATION_ATTEMPT: _closure_attempt,
        SourceAnalysisArtifact.COMPILE_MANIFEST: _compile_manifest,
        SourceAnalysisArtifact.COMPILATION_DATABASE: _compilation_database,
        SourceAnalysisArtifact.MATERIALS_MANIFEST: _materials_manifest,
        SourceAnalysisArtifact.KNOWLEDGE_REVISION: _knowledge_revision,
    }
)
