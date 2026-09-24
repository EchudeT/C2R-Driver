from __future__ import annotations

from .models import ActorRole, EvaluationMode, ProjectConfig, WorkflowError


def validate_project_config(config: ProjectConfig) -> None:
    if not all(
        value.strip()
        for value in (
            config.project_id,
            config.source_platform,
            config.target_platform,
            config.driver_name,
        )
    ):
        raise WorkflowError("project, source, target, and driver names must be non-empty")
    if (
        config.actor_role is ActorRole.DEVELOPER
        and config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE
    ):
        raise WorkflowError("developer role cannot claim a blind evaluation mode")
    if (
        config.actor_role in {ActorRole.CURATOR, ActorRole.EVALUATOR, ActorRole.AUDITOR}
        and config.evaluation_mode is EvaluationMode.DEVELOPER_EVIDENCE
    ):
        raise WorkflowError("curator/evaluator/auditor role requires a blind evaluation mode")
    if not isinstance(config.enable_analysis_review, bool):
        raise WorkflowError("enable_analysis_review must be boolean")
    if not isinstance(config.enable_final_evidence_review, bool):
        raise WorkflowError("enable_final_evidence_review must be boolean")
    if (
        config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE
        and not config.enable_final_evidence_review
    ):
        raise WorkflowError(
            "final_evidence_review cannot be disabled for blind candidate workflows"
        )
