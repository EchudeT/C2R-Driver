from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..core.models import ActorRole, WorkflowError


@dataclass(frozen=True, slots=True)
class PromptDocument:
    relative_path: str
    digest: str
    content: str


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    text: str
    digest: str
    documents: tuple[PromptDocument, ...]


_OPEN = "open-kernel-driver-port"
_MIGRATION = "knowledge-guided-driver-port"
_BLIND = "blind-c2rust-driver-evaluation"

STAGE_DOCUMENTS: dict[str, tuple[str, ...]] = {
    "driver_candidate_resolution": (
        f"{_OPEN}/SKILL.md",
        f"{_OPEN}/references/intake.md",
    ),
    "scope_confirmation": (f"{_OPEN}/SKILL.md", f"{_OPEN}/references/intake.md"),
    "migration_envelope_freeze": (
        f"{_OPEN}/SKILL.md",
        f"{_OPEN}/references/intake.md",
    ),
    "revision_selection": (f"{_OPEN}/SKILL.md", f"{_OPEN}/references/acquisition.md"),
    "evidence_acquisition": (f"{_OPEN}/SKILL.md", f"{_OPEN}/references/acquisition.md"),
    "environment_recovery": (f"{_OPEN}/SKILL.md", f"{_OPEN}/references/environment-recovery.md"),
    "knowledge_base": (f"{_OPEN}/SKILL.md", f"{_OPEN}/references/knowledge-bootstrap.md"),
    "target_platform_study": (
        f"{_MIGRATION}/SKILL.md",
        f"{_MIGRATION}/references/target-platform-study.md",
        f"{_MIGRATION}/references/knowledge-contract.md",
    ),
    "source_closure": (
        f"{_MIGRATION}/SKILL.md",
        f"{_MIGRATION}/references/workflow.md",
        f"{_MIGRATION}/references/translation.md",
    ),
    "structured_c_analysis": (
        f"{_MIGRATION}/SKILL.md",
        f"{_MIGRATION}/references/translation.md",
    ),
    "migration_contracts": (
        f"{_MIGRATION}/SKILL.md",
        f"{_MIGRATION}/references/translation.md",
        f"{_MIGRATION}/references/knowledge-contract.md",
    ),
    "test_adaptation": (
        f"{_MIGRATION}/SKILL.md",
        f"{_MIGRATION}/references/test-porting.md",
    ),
    "rust_design": (
        f"{_MIGRATION}/SKILL.md",
        f"{_MIGRATION}/references/translation.md",
        f"{_MIGRATION}/references/target-changes.md",
    ),
    "rust_implementation": (
        f"{_MIGRATION}/SKILL.md",
        f"{_MIGRATION}/references/translation.md",
        f"{_MIGRATION}/references/target-changes.md",
    ),
    "target_compliance": (
        f"{_MIGRATION}/SKILL.md",
        f"{_MIGRATION}/references/target-changes.md",
        f"{_MIGRATION}/references/target-platform-study.md",
    ),
    "artifact_preparation": (
        f"{_MIGRATION}/SKILL.md",
        f"{_MIGRATION}/references/qemu-evidence.md",
    ),
    "public_qemu_validation": (
        f"{_MIGRATION}/SKILL.md",
        f"{_MIGRATION}/references/qemu-evidence.md",
    ),
    "public_repair": (
        f"{_MIGRATION}/SKILL.md",
        f"{_MIGRATION}/references/workflow.md",
        f"{_MIGRATION}/references/qemu-evidence.md",
    ),
    "completion_audit": (
        f"{_MIGRATION}/SKILL.md",
        f"{_MIGRATION}/references/workflow.md",
        f"{_MIGRATION}/references/knowledge-contract.md",
    ),
    "driver_split": (f"{_BLIND}/SKILL.md", f"{_BLIND}/references/curator-workflow.md"),
    "contract_freeze": (
        f"{_BLIND}/SKILL.md",
        f"{_BLIND}/references/contracts-and-control-plane.md",
        f"{_BLIND}/references/curator-workflow.md",
    ),
    "reference_calibration": (
        f"{_BLIND}/SKILL.md",
        f"{_BLIND}/references/curator-workflow.md",
    ),
    "private_bundle_sealing": (
        f"{_BLIND}/SKILL.md",
        f"{_BLIND}/references/curator-workflow.md",
    ),
    "evaluation_report": (
        f"{_BLIND}/SKILL.md",
        f"{_BLIND}/references/evaluator-workflow.md",
        f"{_BLIND}/references/reporting.md",
    ),
}


class SkillPromptComposer:
    """Compose stage prompts from exact upstream Skill documents."""

    def __init__(self, skill_root: Path) -> None:
        self.skill_root = skill_root.resolve()
        if not self.skill_root.is_dir():
            raise WorkflowError(f"Skill root does not exist: {self.skill_root}")

    def _read_document(self, relative_path: str) -> PromptDocument:
        path = (self.skill_root / relative_path).resolve()
        if self.skill_root not in path.parents:
            raise WorkflowError(f"Skill document escapes root: {relative_path}")
        if not path.is_file():
            raise WorkflowError(f"required Skill document is missing: {path}")
        content = path.read_text(encoding="utf-8")
        return PromptDocument(
            relative_path=relative_path,
            digest=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            content=content,
        )

    def documents_for_stage(self, stage: str) -> tuple[PromptDocument, ...]:
        paths = STAGE_DOCUMENTS.get(stage)
        if not paths:
            raise WorkflowError(f"stage {stage} has no Codex Skill prompt mapping")
        return tuple(self._read_document(path) for path in paths)

    def render(
        self,
        *,
        stage: str,
        actor_role: ActorRole,
        objective: str,
        context: dict[str, object] | None = None,
    ) -> RenderedPrompt:
        documents = self.documents_for_stage(stage)
        header = {
            "stage": stage,
            "actor_role": actor_role.value,
            "objective": objective,
            "context": context or {},
            "skill_documents": [
                {"path": document.relative_path, "sha256": document.digest}
                for document in documents
            ],
        }
        pieces = [
            "You are executing one bounded Driver Port Factory stage.",
            "The controller, not you, owns workflow state and gate outcomes.",
            "Follow the supplied upstream Skill text as normative task instructions.",
            "<job>\n" + json.dumps(header, ensure_ascii=False, sort_keys=True, indent=2) + "\n</job>",
        ]
        for document in documents:
            pieces.append(
                f'<skill_document path="{document.relative_path}" sha256="{document.digest}">\n'
                f"{document.content}\n</skill_document>"
            )
        text = "\n\n".join(pieces) + "\n"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return RenderedPrompt(text=text, digest=digest, documents=documents)
