from __future__ import annotations

import pytest

from driver_port_factory.core.models import (
    ActorRole,
    EvaluationMode,
    ProjectConfig,
    WorkflowError,
)
from driver_port_factory.core.policy import validate_project_config
from driver_port_factory.orchestration.migration import migration_workflow


def config(*, analysis: bool = True, public: bool = True, mode=EvaluationMode.DEVELOPER_EVIDENCE):
    return ProjectConfig(
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


def test_missing_flags_preserve_existing_defaults():
    value = config().to_dict()
    value.pop("enable_analysis_review")
    value.pop("enable_final_evidence_review")
    restored = ProjectConfig.from_dict(value)
    assert restored.enable_analysis_review is True
    assert restored.enable_final_evidence_review is True


def test_blind_candidate_requires_final_evidence_review():
    value = config(
        public=False,
        mode=EvaluationMode.PROSPECTIVE_BLIND,
    )
    with pytest.raises(WorkflowError, match="final_evidence_review cannot be disabled"):
        validate_project_config(value)
