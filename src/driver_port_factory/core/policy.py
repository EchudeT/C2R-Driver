from __future__ import annotations

from .models import ActorRole, EvaluationMode, ProjectConfig, WorkflowError


def _translation_options(config: ProjectConfig) -> None:
    if type(config.scoped_worker_sessions) is not bool:
        raise WorkflowError("scoped_worker_sessions must be boolean")
    if config.behavior_scope is not None:
        from ..intake.behavior_scope import validate

        validate(config.behavior_scope)


def validate_project_config(config: ProjectConfig) -> None:
    _translation_options(config)
    if config.platform_image is not None or config.platform_accelerator is not None:
        if not isinstance(config.platform_image, str) or not config.platform_image.strip():
            raise WorkflowError("platform_image must be a nonempty string")
        if config.platform_accelerator not in {"kvm", "tcg"}:
            raise WorkflowError("platform_accelerator must be kvm or tcg")
    if type(config.compact_paths) is not bool:
        raise WorkflowError("compact_paths must be boolean")
    if type(config.managed_platform) is not bool:
        raise WorkflowError("managed_platform must be boolean")
    if type(config.unified_implementation) is not bool:
        raise WorkflowError("unified_implementation must be boolean")
    if type(config.behavior_scheduling) is not bool:
        raise WorkflowError("behavior_scheduling must be boolean")
    if config.behavior_scheduling and config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE:
        raise WorkflowError("Behavior scheduling pilot currently requires developer-evidence mode")
    if config.benchmark is not None:
        from ..migration.benchmark import validate_spec
        validate_spec(config.benchmark)
        if config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE:
            raise WorkflowError("Configured public benchmark currently requires developer-evidence mode")
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
