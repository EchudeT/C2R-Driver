from __future__ import annotations

from types import MappingProxyType

from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, BundleValidator, json_object, json_value
from .bundle_validation import validate_structured_bundle
from .closure_bundle_validation import validate_source_closure_bundle
from .contracts import SourceAnalysisArtifact, SourceAnalysisStage
from .fact_model import RawFactKind, StructuredAnalysisStatus


def _analysis_report(data: bytes) -> None:
    value = json_object(data, SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_REPORT.value)
    if StructuredAnalysisStatus(value.get("status")) is not StructuredAnalysisStatus.READY:
        raise WorkflowError("structured_c_analysis_report must be READY")
    if value.get("errors"):
        raise WorkflowError("structured_c_analysis_report must not contain errors")


def _analysis_attempt(data: bytes) -> None:
    value = json_object(data, SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_ATTEMPT.value)
    if StructuredAnalysisStatus(value.get("status")) is not StructuredAnalysisStatus.FAIL:
        raise WorkflowError("structured_c_analysis_attempt must record FAIL")
    if not isinstance(value.get("errors"), list) or not value["errors"]:
        raise WorkflowError("structured_c_analysis_attempt must preserve errors")


def _command_records(data: bytes) -> None:
    value = json_value(data, SourceAnalysisArtifact.STRUCTURED_C_COMMAND_RECORDS.value)
    if not isinstance(value, list) or not value:
        raise WorkflowError("structured_c_command_records must be a non-empty array")
    if not all(isinstance(record, dict) for record in value):
        raise WorkflowError("structured_c_command_records contains a non-object record")
    required = {
        "schema_version",
        "unit_id",
        "source_path",
        "fact_kind",
        "capture_format",
        "output_stream",
        "output_sha256",
        "output_size",
        "analyzer_sha256",
        "target_triple",
        "argv",
        "cwd",
        "started_at",
        "completed_at",
        "exit_code",
        "launched",
        "launch_error",
        "timed_out",
        "duration_milliseconds",
        "stdout_sha256",
        "stderr_sha256",
        "stdout_path",
        "stderr_path",
    }
    if any(set(record) != required for record in value):
        raise WorkflowError("structured_c_command_records has an incomplete record schema")
    try:
        kinds = [RawFactKind(record["fact_kind"]) for record in value]
    except (TypeError, ValueError) as error:
        raise WorkflowError("structured_c_command_records has an invalid fact kind") from error
    if len(kinds) != len(set(kinds)) or set(kinds) != set(RawFactKind):
        raise WorkflowError("structured_c_command_records must cover every fact kind once")
    if any(not _successful_command(record) for record in value):
        raise WorkflowError("structured_c_command_records contains an unsuccessful command")


def _successful_command(record: dict[str, object]) -> bool:
    return bool(
        record["schema_version"] == 1
        and record["exit_code"] == 0
        and record["launched"] is True
        and record["launch_error"] is None
        and record["timed_out"] is False
        and isinstance(record["argv"], list)
        and record["argv"]
        and isinstance(record["unit_id"], str)
        and record["unit_id"]
        and isinstance(record["source_path"], str)
        and record["source_path"]
        and isinstance(record["cwd"], str)
        and record["cwd"]
        and isinstance(record["output_size"], int)
        and record["output_size"] >= 0
        and all(
            isinstance(record[field], str) and len(record[field]) == 64
            for field in (
                "output_sha256",
                "analyzer_sha256",
                "stdout_sha256",
                "stderr_sha256",
            )
        )
    )


VALIDATORS = MappingProxyType[SourceAnalysisArtifact, ArtifactValidator](
    {
        SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_REPORT: _analysis_report,
        SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_ATTEMPT: _analysis_attempt,
        SourceAnalysisArtifact.STRUCTURED_C_COMMAND_RECORDS: _command_records,
    }
)

BUNDLE_VALIDATORS = MappingProxyType[SourceAnalysisStage, BundleValidator](
    {
        SourceAnalysisStage.SOURCE_CLOSURE: validate_source_closure_bundle,
        SourceAnalysisStage.STRUCTURED_C_ANALYSIS: validate_structured_bundle,
    }
)
