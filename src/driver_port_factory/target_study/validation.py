from __future__ import annotations

from types import MappingProxyType

from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, json_object, utf8_document
from .contracts import TargetProfileStatus, TargetStudyArtifact, TargetStudyOutcome


def _profile(data: bytes) -> None:
    utf8_document(data)
    text = data.decode("utf-8")
    status_marker = f"Profile status: `{TargetProfileStatus.READY.value}`"
    if "# Target-Platform Profile" not in text or status_marker not in text:
        raise WorkflowError("target_profile must be a completed READY profile")


def _structured_profile(data: bytes) -> None:
    value = json_object(data, TargetStudyArtifact.STRUCTURED_PROFILE.value)
    if value.get("schema_version") != 1:
        raise WorkflowError("target_profile_structured must be schema_version=1")
    if TargetProfileStatus(value.get("profile_status")) is not TargetProfileStatus.READY:
        raise WorkflowError("target_profile_structured must be READY")


def _api_evidence(data: bytes) -> None:
    value = json_object(data, TargetStudyArtifact.API_EVIDENCE.value)
    if value.get("schema_version") != 1 or not value.get("entries"):
        raise WorkflowError("target_api_evidence requires at least one API entry")


def _analogous_trace(data: bytes) -> None:
    value = json_object(data, TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE.value)
    if value.get("schema_version") != 1 or not value.get("steps"):
        raise WorkflowError("analogous_driver_trace requires ordered trace steps")


def _change_plan(data: bytes) -> None:
    value = json_object(data, TargetStudyArtifact.CHANGE_PLAN.value)
    if value.get("schema_version") != 1 or not value.get("integration_path"):
        raise WorkflowError("target_change_plan requires an integration path")


def _report(data: bytes) -> None:
    value = json_object(data, TargetStudyArtifact.REPORT.value)
    if TargetStudyOutcome(value.get("status")) is not TargetStudyOutcome.PASS or value.get(
        "errors"
    ):
        raise WorkflowError("target_study_report must pass without validation errors")


def _failed_attempt(data: bytes) -> None:
    value = json_object(data, TargetStudyArtifact.VALIDATION_ATTEMPT.value)
    if TargetStudyOutcome(value.get("status")) is not TargetStudyOutcome.FAIL:
        raise WorkflowError("target_study_validation_attempt must record FAIL")
    if not value.get("errors"):
        raise WorkflowError("target_study_validation_attempt must preserve errors")


VALIDATORS = MappingProxyType[TargetStudyArtifact, ArtifactValidator](
    {
        TargetStudyArtifact.PROFILE: _profile,
        TargetStudyArtifact.STRUCTURED_PROFILE: _structured_profile,
        TargetStudyArtifact.API_EVIDENCE: _api_evidence,
        TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE: _analogous_trace,
        TargetStudyArtifact.CHANGE_PLAN: _change_plan,
        TargetStudyArtifact.REPORT: _report,
        TargetStudyArtifact.VALIDATION_ATTEMPT: _failed_attempt,
    }
)
