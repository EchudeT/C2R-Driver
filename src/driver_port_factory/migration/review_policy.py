"""Worker self-check protocol, separate from independent functional review."""
from pathlib import Path

from ..codex.contracts import CodexOutputError


def require_self_review(text: str) -> None:
    if not text.strip():
        raise CodexOutputError(
            "Complete the Skill's self-check in the report before submitting the pass decision. "
            "A self-check does not replace the independent final review."
        )


def review_policy_digest(project, skill_root: Path | None, stage) -> str:
    """Hash only the rules loaded for one independent review stage."""
    from ..codex.prompts import SkillPromptComposer
    from ..composition import WORKFLOW_STAGE_CATALOG

    composer = SkillPromptComposer(
        skill_root or Path(project.config.skill_root),
        WORKFLOW_STAGE_CATALOG,
        project.workflow.stage_values,
        Path(project.config.prompt_pack) if project.config.prompt_pack else None,
    )
    return composer.policy_digest(stage)
