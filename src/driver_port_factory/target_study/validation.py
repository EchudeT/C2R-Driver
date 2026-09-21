from __future__ import annotations

from types import MappingProxyType

from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, json_object, utf8_document
from .contracts import TargetStudyArtifact, TargetStudyOutcome


def _failed_attempt(data: bytes) -> None:
    value = json_object(data, TargetStudyArtifact.VALIDATION_ATTEMPT.value)
    if TargetStudyOutcome(value.get("status")) is not TargetStudyOutcome.FAIL:
        raise WorkflowError("target_study_validation_attempt must record FAIL")
    if not value.get("errors"):
        raise WorkflowError("target_study_validation_attempt must preserve errors")


VALIDATORS = MappingProxyType[TargetStudyArtifact, ArtifactValidator](
    {
        TargetStudyArtifact.REPORT: utf8_document,
        TargetStudyArtifact.VALIDATION_ATTEMPT: _failed_attempt,
    }
)
