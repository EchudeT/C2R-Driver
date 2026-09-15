from __future__ import annotations

import json
from pathlib import Path

from .acquisition.contracts import AcquisitionStage
from .acquisition.validation import VALIDATORS as ACQUISITION_VALIDATORS
from .codex.validation import VALIDATORS as CODEX_VALIDATORS
from .control.contracts import ControlArtifact, ControlStage
from .control.validation import VALIDATORS as CONTROL_VALIDATORS
from .core.models import ActorRole, ProjectConfig, WorkflowError
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
from .migration.validation import VALIDATORS as MIGRATION_VALIDATORS
from .orchestration.blind import auditor_workflow, curator_workflow, evaluator_workflow
from .orchestration.migration import migration_workflow
from .sealing.contracts import SealingStage
from .sealing.validation import VALIDATORS as SEALING_VALIDATORS
from .source_analysis.analysis_validation import (
    BUNDLE_VALIDATORS as SOURCE_BUNDLE_VALIDATORS,
)
from .source_analysis.analysis_validation import VALIDATORS as ANALYSIS_VALIDATORS
from .source_analysis.closure_validation import VALIDATORS as CLOSURE_VALIDATORS
from .source_analysis.contracts import SourceAnalysisStage
from .source_analysis.fact_validation import VALIDATORS as FACT_VALIDATORS
from .source_analysis.semantic_validation import VALIDATORS as SEMANTIC_VALIDATORS
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
        CLOSURE_VALIDATORS,
        FACT_VALIDATORS,
        SEMANTIC_VALIDATORS,
        ANALYSIS_VALIDATORS,
        MIGRATION_VALIDATORS,
        SEALING_VALIDATORS,
        EVALUATION_VALIDATORS,
        CODEX_VALIDATORS,
    ),
    (SOURCE_BUNDLE_VALIDATORS,),
)

WORKFLOW_STAGE_CATALOG = StageCatalog.compose(
    (
        ControlStage,
        IntakeStage,
        AcquisitionStage,
        EnvironmentStage,
        KnowledgeStage,
        TargetStudyStage,
        SourceAnalysisStage,
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
    return WorkflowDefinition.build(specs, ARTIFACT_VALIDATORS)


def initialize_project(root: Path, config: ProjectConfig) -> Project:
    return Project.initialize(
        root,
        config,
        workflow_for(config),
        ARTIFACT_VALIDATORS,
        initial_stage=ControlStage.PROJECT_INIT,
        manifest_kind=ControlArtifact.PROJECT_MANIFEST,
    )


def open_project(root: Path) -> Project:
    resolved = root.resolve()
    config_path = resolved / Project.CONTROL_DIR / "project.json"
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError(f"cannot load project configuration: {config_path}") from error
    if not isinstance(value, dict):
        raise WorkflowError("project configuration must be a JSON object")
    config = ProjectConfig.from_dict(value)
    project = Project(resolved, workflow_for(config), ARTIFACT_VALIDATORS)
    project.verify_integrity()
    return project
