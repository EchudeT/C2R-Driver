from __future__ import annotations

import json
from pathlib import Path

from .acquisition.artifact_validation import VALIDATORS as ACQUISITION_VALIDATORS
from .acquisition.bundle_validation import (
    BUNDLE_VALIDATORS as ACQUISITION_BUNDLE_VALIDATORS,
)
from .acquisition.contracts import AcquisitionStage
from .codex.validation import VALIDATORS as CODEX_VALIDATORS
from .control.contracts import ControlArtifact, ControlStage
from .control.validation import VALIDATORS as CONTROL_VALIDATORS
from .core.models import ActorRole, EvaluationMode, ProjectConfig, WorkflowError
from .core.project import Project
from .core.validation import ValidationRegistry
from .core.workflow import StageCatalog, WorkflowDefinition
from .environment.contracts import EnvironmentStage
from .environment.validation import VALIDATORS as ENVIRONMENT_VALIDATORS
from .evaluation.contracts import EvaluationStage
from .evaluation.validation import VALIDATORS as EVALUATION_VALIDATORS
from .intake.contracts import IntakeStage
from .intake.validation import VALIDATORS as INTAKE_VALIDATORS
from .knowledge.contracts import KnowledgeStage
from .knowledge.validation import VALIDATORS as KNOWLEDGE_VALIDATORS
from .migration.contracts import MigrationStage
from .migration.validation import BUNDLE_VALIDATORS as MIGRATION_BUNDLE_VALIDATORS
from .migration.validation import VALIDATORS as MIGRATION_VALIDATORS
from .orchestration.blind import auditor_workflow, curator_workflow, evaluator_workflow
from .orchestration.migration import migration_workflow
from .sealing.contracts import SealingStage
from .sealing.validation import BUNDLE_VALIDATORS as SEALING_BUNDLE_VALIDATORS
from .sealing.validation import VALIDATORS as SEALING_VALIDATORS
from .target_study.contracts import TargetStudyStage
from .target_study.validation import VALIDATORS as TARGET_STUDY_VALIDATORS

ARTIFACT_VALIDATORS = ValidationRegistry.compose(
    (
        CONTROL_VALIDATORS,
        INTAKE_VALIDATORS,
        ACQUISITION_VALIDATORS,
        ENVIRONMENT_VALIDATORS,
        KNOWLEDGE_VALIDATORS,
        TARGET_STUDY_VALIDATORS,
        MIGRATION_VALIDATORS,
        SEALING_VALIDATORS,
        EVALUATION_VALIDATORS,
        CODEX_VALIDATORS,
    ),
    (
        ACQUISITION_BUNDLE_VALIDATORS,
        MIGRATION_BUNDLE_VALIDATORS,
        SEALING_BUNDLE_VALIDATORS,
    ),
)

WORKFLOW_STAGE_CATALOG = StageCatalog.compose(
    (
        ControlStage,
        IntakeStage,
        AcquisitionStage,
        EnvironmentStage,
        KnowledgeStage,
        TargetStudyStage,
        MigrationStage,
        SealingStage,
        EvaluationStage,
    )
)


def workflow_for(config: ProjectConfig) -> WorkflowDefinition:
    if config.actor_role in {ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR}:
        specs = migration_workflow(config)
    elif config.actor_role is ActorRole.CURATOR:
        specs = curator_workflow(config)
    elif config.actor_role is ActorRole.EVALUATOR:
        specs = evaluator_workflow(config)
    elif config.actor_role is ActorRole.AUDITOR:
        specs = auditor_workflow(config)
    else:
        raise WorkflowError(f"unsupported actor role: {config.actor_role.value}")
    from dataclasses import replace

    from .codex.contracts import CodexArtifact
    from .core.models import EvaluationMode, StageOwner
    if config.evaluation_mode is EvaluationMode.DEVELOPER_EVIDENCE:
        # Static stages call the same worker only when a checker needs a decision.
        specs = tuple(replace(spec, auxiliary_outputs=(*spec.auxiliary_outputs,
                      CodexArtifact.JOB_RESULT, CodexArtifact.WORK_REPORT,
                      CodexArtifact.SUBMISSION, CodexArtifact.EVENT_LOG))
                      if spec.owner is StageOwner.STATIC else spec for spec in specs)
    return WorkflowDefinition.build(specs, ARTIFACT_VALIDATORS)


def initialize_project(root: Path, config: ProjectConfig) -> Project:
    project = Project.initialize(
        root,
        config,
        workflow_for(config),
        ARTIFACT_VALIDATORS,
        initial_stage=ControlStage.PROJECT_INIT,
        manifest_kind=ControlArtifact.PROJECT_MANIFEST,
    )
    if config.evaluation_mode is EvaluationMode.DEVELOPER_EVIDENCE:
        import fcntl

        from .codex.context_policy import configure_policy
        # Initialization is not a controller execution interval for cost/timing reports.
        with (project.control / "controller.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            configure_policy(project, "analysis-handoff", reason="new project default")
    return project


def open_project(root: Path, *, read_only: bool = False,
                 verify_artifacts: bool = True) -> Project:
    resolved = root.resolve()
    config_path = resolved / Project.CONTROL_DIR / "project.json"
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError(f"cannot load project configuration: {config_path}") from error
    if not isinstance(value, dict):
        raise WorkflowError("project configuration must be a JSON object")
    config = ProjectConfig.from_dict(value)
    project = Project(
        resolved,
        workflow_for(config),
        ARTIFACT_VALIDATORS,
        read_only=read_only,
    )
    # Scoped readers verify the actual evidence they consume. Mutating commands
    # and explicit audits still perform the full content check by default.
    if not verify_artifacts and not read_only:
        raise WorkflowError("scoped integrity is restricted to read-only consumers")
    project.verify_integrity(artifacts=verify_artifacts)
    return project
