from __future__ import annotations

from ..core.models import ActorRole, StageStatus, WorkflowError
from ..core.project import Project
from .contracts import KnowledgeStage

KNOWLEDGE_ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)


def ensure_knowledge_stage_running(project: Project) -> None:
    project.ensure_role(*KNOWLEDGE_ROLES)
    stage = project.stage(KnowledgeStage.KNOWLEDGE_BASE)
    if stage.status is StageStatus.READY:
        project.start(KnowledgeStage.KNOWLEDGE_BASE)
    elif stage.status is not StageStatus.RUNNING:
        raise WorkflowError(f"knowledge_base must be READY or RUNNING, got {stage.status.value}")
