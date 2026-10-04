from __future__ import annotations

from driver_port_factory.core.models import (
    ActorRole,
    EvaluationMode,
    ProjectConfig,
)
from driver_port_factory.core.policy import validate_project_config
from driver_port_factory.orchestration.migration import migration_workflow


def config(*, analysis: bool = True, public: bool = True, mode=EvaluationMode.DEVELOPER_EVIDENCE):
    return ProjectConfig(
        unified_implementation=False,
        project_id="optional-reviews",
        source_platform="source",
        target_platform="target",
        driver_name="driver",
        evaluation_mode=mode,
        actor_role=(
            ActorRole.DEVELOPER
            if mode is EvaluationMode.DEVELOPER_EVIDENCE
            else ActorRole.MIGRATION_OPERATOR
        ),
        enable_analysis_review=analysis,
        enable_final_evidence_review=public,
    )


def stage_names(value: ProjectConfig) -> list[str]:
    return [stage.name.value for stage in migration_workflow(value)]


def test_developer_review_stages_are_independently_optional():
    names = stage_names(config(analysis=False, public=False))
    assert "analysis_review" not in names
    assert "final_evidence_review" not in names
    assert names[-1] == "public_qemu_validation"

    names = stage_names(config(analysis=True, public=False))
    assert "analysis_review" in names
    assert "final_evidence_review" not in names

    names = stage_names(config(analysis=False, public=True))
    assert "analysis_review" not in names
    assert "final_evidence_review" in names


def test_missing_review_flags_use_new_defaults():
    value = config().to_dict()
    value.pop("enable_analysis_review")
    value.pop("enable_final_evidence_review")
    restored = ProjectConfig.from_dict(value)
    assert restored.enable_analysis_review is False
    assert restored.enable_final_evidence_review is False


def test_blind_candidate_does_not_require_an_extra_model_reviewer():
    value = config(
        public=False,
        mode=EvaluationMode.PROSPECTIVE_BLIND,
    )
    validate_project_config(value)


def test_delivery_with_both_reviews_disabled_keeps_real_execution_gates(tmp_path):
    """Offline synthetic executor flow; no paid models or actual driver validation."""
    from dataclasses import replace
    from unittest.mock import patch

    from driver_port_factory.composition import open_project
    from driver_port_factory.migration.contracts import MigrationArtifact as A
    from driver_port_factory.migration.contracts import MigrationStage as S
    from tests.migration_support import accepted

    def without_review(**kwargs):
        return replace(
            ProjectConfig(**kwargs),
            enable_analysis_review=False,
            enable_final_evidence_review=False,
        )

    with patch("tests.knowledge_support.ProjectConfig", without_review):
        project, _, _ = accepted(tmp_path)
    assert S.ANALYSIS_REVIEW.value not in project.workflow.stage_values
    assert S.FINAL_EVIDENCE_REVIEW.value not in project.workflow.stage_values
    assert project.stages()[-1].status.value == "PASS"
    public = project.load_json_artifact(S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_REPORT)
    assert public["run"]["execution_status"] == "PASS"
    assert public["run"]["exec_trace"]["runtime_bound"] is True
    open_project(project.root).verify_integrity()
