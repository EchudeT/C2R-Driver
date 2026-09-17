from __future__ import annotations

from types import MappingProxyType

from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, BundleValidator, json_object
from .bundle_validation import validate_structured_bundle
from .closure_bundle_validation import validate_source_closure_bundle
from .contracts import SourceAnalysisArtifact, SourceAnalysisStage
from .fact_model import StructuredAnalysisStatus


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


VALIDATORS = MappingProxyType[SourceAnalysisArtifact, ArtifactValidator](
    {
        SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_REPORT: _analysis_report,
        SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_ATTEMPT: _analysis_attempt,
    }
)

BUNDLE_VALIDATORS = MappingProxyType[SourceAnalysisStage, BundleValidator](
    {
        SourceAnalysisStage.SOURCE_CLOSURE: validate_source_closure_bundle,
        SourceAnalysisStage.STRUCTURED_C_ANALYSIS: validate_structured_bundle,
    }
)
