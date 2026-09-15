from __future__ import annotations

from types import MappingProxyType

from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, json_object, require_fields
from .contracts import IntakeArtifact, IntakeStatus, MetadataScope


def _request_record(data: bytes) -> None:
    value = json_object(data, IntakeArtifact.REQUEST_RECORD.value)
    require_fields(
        value,
        {
            "source_platform",
            "target_platform",
            "user_supplied_driver_name",
            "raw_request",
            "intake_status",
        },
        IntakeArtifact.REQUEST_RECORD.value,
    )
    IntakeStatus(value["intake_status"])


def _driver_candidates(data: bytes) -> None:
    value = json_object(data, IntakeArtifact.DRIVER_CANDIDATES.value)
    if not isinstance(value.get("candidates"), list):
        raise WorkflowError("driver_candidates.candidates must be a list")
    if MetadataScope(value.get("metadata_scope")) is not MetadataScope.LIGHTWEIGHT_ONLY:
        raise WorkflowError("driver candidate resolution must remain lightweight before clone")


def _confirmation_question(data: bytes) -> None:
    value = json_object(data, IntakeArtifact.CONFIRMATION_QUESTION.value)
    if not str(value.get("question", "")).strip() or value.get("question_count") != 1:
        raise WorkflowError("confirmation_question must contain exactly one question")
    if IntakeStatus(value.get("intake_status")) is not IntakeStatus.WAITING_FOR_USER:
        raise WorkflowError("confirmation_question must record WAITING_FOR_USER")


def _scope_confirmation(data: bytes) -> None:
    value = json_object(data, IntakeArtifact.SCOPE_CONFIRMATION.value)
    if IntakeStatus(value.get("identity_status")) is not IntakeStatus.CONFIRMED:
        raise WorkflowError("scope_confirmation must have confirmed identity")
    if not isinstance(value.get("selected_candidate"), dict):
        raise WorkflowError("scope_confirmation requires a selected_candidate")


def _migration_envelope(data: bytes) -> None:
    value = json_object(data, IntakeArtifact.MIGRATION_ENVELOPE.value)
    require_fields(
        value,
        {
            "source_platform",
            "target_platform",
            "canonical_source_driver_name",
            "source_driver_entry_or_repository_hint",
            "device_family",
            "bus_or_transport",
            "intended_subset",
            "excluded_variants",
            "identity_status",
        },
        IntakeArtifact.MIGRATION_ENVELOPE.value,
    )
    if IntakeStatus(value["identity_status"]) is not IntakeStatus.FROZEN:
        raise WorkflowError("migration_envelope identity must be frozen")
    if not isinstance(value["intended_subset"], list) or not value["intended_subset"]:
        raise WorkflowError("migration_envelope.intended_subset must be non-empty")


def _identity_record(data: bytes) -> None:
    value = json_object(data, IntakeArtifact.IDENTITY_RECORD.value)
    require_fields(
        value,
        {"canonical_name", "bus", "device_scope", "source_paths", "confirmed"},
        IntakeArtifact.IDENTITY_RECORD.value,
    )
    if value["confirmed"] is not True:
        raise WorkflowError("identity_record must explicitly set confirmed=true")
    for field in ("device_scope", "source_paths"):
        if not isinstance(value[field], list) or not value[field]:
            raise WorkflowError(f"identity_record.{field} must be a non-empty list")


VALIDATORS = MappingProxyType[IntakeArtifact, ArtifactValidator](
    {
        IntakeArtifact.REQUEST_RECORD: _request_record,
        IntakeArtifact.DRIVER_CANDIDATES: _driver_candidates,
        IntakeArtifact.CONFIRMATION_QUESTION: _confirmation_question,
        IntakeArtifact.SCOPE_CONFIRMATION: _scope_confirmation,
        IntakeArtifact.MIGRATION_ENVELOPE: _migration_envelope,
        IntakeArtifact.IDENTITY_RECORD: _identity_record,
    }
)
