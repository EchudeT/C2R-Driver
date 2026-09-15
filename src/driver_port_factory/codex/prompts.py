from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.models import ActorRole, WorkflowError


@dataclass(frozen=True, slots=True)
class PromptDocument:
    relative_path: str
    digest: str
    content: str


@dataclass(frozen=True, slots=True)
class PromptPack:
    name: str
    root: Path
    manifest_digest: str
    template_path: Path
    template_digest: str
    template: str
    stages: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    text: str
    digest: str
    documents: tuple[PromptDocument, ...]
    prompt_pack_name: str
    prompt_pack_manifest_digest: str
    prompt_template_digest: str


def default_prompt_pack_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "prompt-packs" / "default"


def load_prompt_pack(path: Path | None = None) -> PromptPack:
    root = (path or default_prompt_pack_path()).resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise WorkflowError(f"prompt pack manifest does not exist: {manifest_path}")
    raw_manifest = manifest_path.read_bytes()
    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError(
            f"prompt pack manifest is not valid UTF-8 JSON: {manifest_path}"
        ) from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise WorkflowError("prompt pack manifest must be a schema_version=1 object")
    name = manifest.get("name")
    template_name = manifest.get("template")
    stage_values = manifest.get("stages")
    if not isinstance(name, str) or not name.strip():
        raise WorkflowError("prompt pack name must be a non-empty string")
    if not isinstance(template_name, str) or not template_name:
        raise WorkflowError("prompt pack template must be a relative path")
    template_path = (root / template_name).resolve()
    if root not in template_path.parents or not template_path.is_file():
        raise WorkflowError(f"prompt pack template is missing or escapes its root: {template_name}")
    template_raw = template_path.read_bytes()
    try:
        template = template_raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkflowError(f"prompt pack template is not UTF-8: {template_path}") from error
    if not isinstance(stage_values, dict):
        raise WorkflowError("prompt pack stages must be an object")
    stages: dict[str, tuple[str, ...]] = {}
    for stage, values in stage_values.items():
        if not isinstance(stage, str) or not stage:
            raise WorkflowError("prompt pack stage names must be non-empty strings")
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(value, str) and value for value in values)
        ):
            raise WorkflowError(f"prompt pack stage {stage} needs a non-empty document list")
        stages[stage] = tuple(values)
    return PromptPack(
        name=name,
        root=root,
        manifest_digest=hashlib.sha256(raw_manifest).hexdigest(),
        template_path=template_path,
        template_digest=hashlib.sha256(template_raw).hexdigest(),
        template=template,
        stages=stages,
    )


class SkillPromptComposer:
    """Compose a stage prompt from an editable prompt pack and current Skill sources."""

    def __init__(self, skill_root: Path, prompt_pack: Path | None = None) -> None:
        self.skill_root = skill_root.resolve()
        if not self.skill_root.is_dir():
            raise WorkflowError(f"Skill root does not exist: {self.skill_root}")
        self.prompt_pack = load_prompt_pack(prompt_pack)

    def _read_document(self, relative_path: str) -> PromptDocument:
        path = (self.skill_root / relative_path).resolve()
        if self.skill_root not in path.parents:
            raise WorkflowError(f"Skill document escapes root: {relative_path}")
        if not path.is_file():
            raise WorkflowError(f"required Skill document is missing: {path}")
        raw = path.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise WorkflowError(f"Skill document is not UTF-8: {path}") from error
        return PromptDocument(
            relative_path=relative_path,
            digest=hashlib.sha256(raw).hexdigest(),
            content=content,
        )

    def documents_for_stage(self, stage: str) -> tuple[PromptDocument, ...]:
        paths = self.prompt_pack.stages.get(stage)
        if not paths:
            raise WorkflowError(
                f"stage {stage} has no document mapping in prompt pack {self.prompt_pack.name}"
            )
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
        header: dict[str, Any] = {
            "stage": stage,
            "actor_role": actor_role.value,
            "objective": objective,
            "context": context or {},
            "prompt_pack": {
                "name": self.prompt_pack.name,
                "manifest_sha256": self.prompt_pack.manifest_digest,
                "template_sha256": self.prompt_pack.template_digest,
            },
            "skill_documents": [
                {"path": document.relative_path, "sha256": document.digest}
                for document in documents
            ],
        }
        embedded_documents = "\n\n".join(
            f'<skill_document path="{document.relative_path}" sha256="{document.digest}">\n'
            f"{document.content}\n</skill_document>"
            for document in documents
        )
        text = self.prompt_pack.template
        for marker, value in {
            "{{job_json}}": json.dumps(header, ensure_ascii=False, sort_keys=True, indent=2),
            "{{skill_documents}}": embedded_documents,
        }.items():
            text = text.replace(marker, value)
        if not text.endswith("\n"):
            text += "\n"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return RenderedPrompt(
            text=text,
            digest=digest,
            documents=documents,
            prompt_pack_name=self.prompt_pack.name,
            prompt_pack_manifest_digest=self.prompt_pack.manifest_digest,
            prompt_template_digest=self.prompt_pack.template_digest,
        )
