from __future__ import annotations

from typing import Any

from ..core.models import WorkflowError
from .contracts import ApprovalStatus, ChangeLevel, InvestigationStatus
from .evidence import TargetEvidenceVerifier, require_fields


class TargetChangePlanValidator:
    """Enforce the Skill's smallest-change and explicit-approval policy."""

    CHANGE_FIELDS = frozenset(
        {
            "change_id",
            "driver_contract_ids",
            "problem_and_observed_blocker",
            "target_evidence",
            "alternatives_considered_and_why_insufficient",
            "selected_change_level",
            "exact_files_and_symbols",
            "smallest_expected_behavioral_effect",
            "public_api_abi_or_safety_impact",
            "validation_plan",
            "rollback_method",
            "status",
            "broad_impact",
            "approval_status",
        }
    )

    def __init__(self, evidence: TargetEvidenceVerifier) -> None:
        self.evidence = evidence

    def validate(self, changes: dict[str, Any]) -> dict[str, Any]:
        require_fields(
            changes,
            {
                "schema_version",
                "integration_path",
                "required_change_level",
                "driver_owned_paths",
                "proposed_preexisting_changes",
                "investigations",
            },
            "target change plan",
        )
        if changes["schema_version"] != 1:
            raise WorkflowError("target change plan schema_version must be 1")
        try:
            required_level = ChangeLevel(changes["required_change_level"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("target change plan has an invalid change level") from error
        if not str(changes["integration_path"]).strip():
            raise WorkflowError("target change plan requires an integration path")
        owned_paths = changes["driver_owned_paths"]
        if not isinstance(owned_paths, list) or not owned_paths:
            raise WorkflowError("target change plan requires driver-owned paths")
        investigations = self._investigations(changes["investigations"])
        resolution_ids = set(investigations)
        proposed = changes["proposed_preexisting_changes"]
        if not isinstance(proposed, list):
            raise WorkflowError("proposed_preexisting_changes must be a list")
        for change in proposed:
            resolution_ids.add(self._validate_change(change))
        if required_level is ChangeLevel.DRIVER_OWNED and proposed:
            raise WorkflowError(
                "driver-owned integration cannot declare pre-existing target changes"
            )
        return {
            "required_change_level": required_level.value,
            "proposed_change_count": len(proposed),
            "resolution_ids": sorted(resolution_ids),
            "investigations": investigations,
        }

    @staticmethod
    def _investigations(value: Any) -> dict[str, InvestigationStatus]:
        if not isinstance(value, list):
            raise WorkflowError("target change investigations must be a list")
        investigations = {}
        for investigation in value:
            investigation = require_fields(
                investigation,
                {"investigation_id", "question", "planned_action", "status"},
                "target investigation",
            )
            try:
                status = InvestigationStatus(investigation["status"])
            except (TypeError, ValueError) as error:
                raise WorkflowError("target investigation has an invalid status") from error
            investigations[str(investigation["investigation_id"])] = status
        return investigations

    def _validate_change(self, value: Any) -> str:
        change = require_fields(value, self.CHANGE_FIELDS, "pre-existing target change")
        contract_ids = change["driver_contract_ids"]
        if (
            not isinstance(contract_ids, list)
            or not contract_ids
            or any(
                not isinstance(identifier, str) or not identifier.strip()
                for identifier in contract_ids
            )
        ):
            raise WorkflowError("pre-existing target change requires planned driver contract IDs")
        try:
            selected_level = ChangeLevel(change["selected_change_level"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("pre-existing target change has an invalid level") from error
        if selected_level is ChangeLevel.DRIVER_OWNED:
            raise WorkflowError("pre-existing target change has an invalid level")
        if change["broad_impact"]:
            self._require_approval(change)
        self.evidence.verify(change["target_evidence"])
        return str(change["change_id"])

    @staticmethod
    def _require_approval(change: dict[str, Any]) -> None:
        try:
            approval = ApprovalStatus(change["approval_status"])
        except (TypeError, ValueError) as error:
            raise WorkflowError(
                f"broad target change {change['change_id']} requires explicit approval"
            ) from error
        if approval is not ApprovalStatus.APPROVED:
            raise WorkflowError(
                f"broad target change {change['change_id']} requires explicit approval"
            )
