from __future__ import annotations

import hashlib
from html import escape
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.contracts import StageKey
from ..core.models import ActorRole, WorkflowError
from ..core.workflow import StageCatalog


@dataclass(frozen=True, slots=True)
class PromptDocument:
    relative_path: str
    digest: str
    content: str


@dataclass(frozen=True, slots=True)
class PromptOutputSchema:
    relative_path: str
    path: Path
    digest: str


@dataclass(frozen=True, slots=True)
class PromptStage:
    documents: tuple[str, ...]
    output_schema: PromptOutputSchema | None
    objective: str | None


@dataclass(frozen=True, slots=True)
class PromptPack:
    name: str
    root: Path
    manifest_digest: str
    template_path: Path
    template_digest: str
    template: str
    correction_template: str
    stages: dict[str, PromptStage]


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    text: str
    digest: str
    objective: str
    documents: tuple[PromptDocument, ...]
    prompt_pack_name: str
    prompt_pack_manifest_digest: str
    prompt_template_digest: str
    output_schema: PromptOutputSchema | None


def default_prompt_pack_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "prompt-packs" / "default"


def _load_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    if not path.is_file():
        raise WorkflowError(f"prompt pack manifest does not exist: {path}")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError(f"prompt pack manifest is not valid UTF-8 JSON: {path}") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise WorkflowError("prompt pack manifest must be a schema_version=1 object")
    return value, raw


def _pack_file(root: Path, value: Any, label: str) -> tuple[Path, bytes]:
    if not isinstance(value, str) or not value:
        raise WorkflowError(f"prompt pack {label} must be a relative path")
    path = (root / value).resolve()
    if root not in path.parents or not path.is_file():
        raise WorkflowError(f"prompt pack {label} is missing or escapes its root: {value}")
    return path, path.read_bytes()


def _prompt_stages(
    value: Any,
    catalog: StageCatalog,
    root: Path,
) -> dict[str, PromptStage]:
    if not isinstance(value, dict):
        raise WorkflowError("prompt pack stages must be an object")
    stages: dict[str, PromptStage] = {}
    for stage, specification in value.items():
        if not isinstance(stage, str) or not stage:
            raise WorkflowError("prompt pack stage names must be non-empty strings")
        if not catalog.contains(stage):
            raise WorkflowError(f"prompt pack contains an unknown stage: {stage}")
        if not isinstance(specification, dict) or set(specification) - {
            "documents",
            "objective",
            "output_schema",
        }:
            raise WorkflowError(f"prompt pack stage {stage} must be a stage specification")
        documents = specification.get("documents")
        if (
            not isinstance(documents, list)
            or not documents
            or not all(isinstance(document, str) and document for document in documents)
        ):
            raise WorkflowError(f"prompt pack stage {stage} needs a non-empty document list")
        output_schema = None
        if "output_schema" in specification:
            relative_path = specification["output_schema"]
            schema_path, schema_raw = _pack_file(root, relative_path, "output schema")
            try:
                schema_document = json.loads(schema_raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise WorkflowError(
                    f"prompt pack output schema is not valid UTF-8 JSON: {schema_path}"
                ) from error
            if not isinstance(schema_document, dict):
                raise WorkflowError("prompt pack output schema must be a JSON object")
            output_schema = PromptOutputSchema(
                relative_path,
                schema_path,
                hashlib.sha256(schema_raw).hexdigest(),
            )
        objective = specification.get("objective")
        if objective is not None and (
            not isinstance(objective, str) or not objective.strip()
        ):
            raise WorkflowError(
                f"prompt pack stage {stage} objective must be a non-empty string"
            )
        stages[stage] = PromptStage(tuple(documents), output_schema, objective)
    return stages


def load_prompt_pack(path: Path | None, stage_catalog: StageCatalog) -> PromptPack:
    root = (path or default_prompt_pack_path()).resolve()
    manifest_path = root / "manifest.json"
    manifest, raw_manifest = _load_manifest(manifest_path)
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        raise WorkflowError("prompt pack name must be a non-empty string")
    template_path, template_raw = _pack_file(root, manifest.get("template"), "template")
    _, correction_raw = _pack_file(root, manifest.get("correction_template"), "correction template")
    try:
        template = template_raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkflowError(f"prompt pack template is not UTF-8: {template_path}") from error
    try:
        correction_template = correction_raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkflowError("prompt pack correction template is not UTF-8") from error
    if "{{error}}" not in correction_template:
        raise WorkflowError("prompt pack correction template is missing {{error}}")
    required_markers = {"{{job_json}}", "{{skill_documents}}"}
    missing_markers = sorted(marker for marker in required_markers if marker not in template)
    if missing_markers:
        raise WorkflowError(
            "prompt pack template is missing structural markers: " + ", ".join(missing_markers)
        )
    stages = _prompt_stages(manifest.get("stages"), stage_catalog, root)
    return PromptPack(
        name=name,
        root=root,
        manifest_digest=hashlib.sha256(raw_manifest).hexdigest(),
        template_path=template_path,
        template_digest=hashlib.sha256(template_raw).hexdigest(),
        template=template,
        correction_template=correction_template,
        stages=stages,
    )


class SkillPromptComposer:
    """Compose a stage prompt from an editable prompt pack and current Skill sources."""

    @staticmethod
    def _executable(stage):
        from ..orchestration.protocol import TASKS
        return stage in TASKS and TASKS[stage].executable

    def __init__(
        self,
        skill_root: Path,
        stage_catalog: StageCatalog,
        allowed_stage_names: tuple[str, ...],
        prompt_pack: Path | None = None,
    ) -> None:
        self.skill_root = skill_root.resolve()
        if not self.skill_root.is_dir():
            raise WorkflowError(f"Skill root does not exist: {self.skill_root}")
        self.allowed_stages = frozenset(allowed_stage_names)
        self.prompt_pack = load_prompt_pack(prompt_pack, stage_catalog)

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

    def documents_for_stage(self, stage: StageKey) -> tuple[PromptDocument, ...]:
        if stage.value not in self.allowed_stages:
            raise WorkflowError(f"stage {stage.value} is outside the current project workflow")
        specification = self.prompt_pack.stages.get(stage.value)
        if specification is None:
            raise WorkflowError(
                f"stage {stage.value} has no document mapping in prompt pack "
                f"{self.prompt_pack.name}"
            )
        return tuple(self._read_document(path) for path in specification.documents)

    def render(
        self,
        *,
        stage: StageKey,
        actor_role: ActorRole,
        objective: str | None = None,
        context: dict[str, object] | None = None,
        known_documents: dict[str, str] | None = None,
    ) -> RenderedPrompt:
        documents = self.documents_for_stage(stage)
        if (context or {}).get("repair_execution"):
            from ..migration.repair_execution import OBJECTIVE
            objective = objective or OBJECTIVE
            extra = "knowledge-guided-driver-port/references/qemu-evidence.md"
            if all(d.relative_path != extra for d in documents):
                documents = (*documents, self._read_document(extra))
        stage_specification = self.prompt_pack.stages[stage.value]
        effective_objective = (
            objective if objective is not None else stage_specification.objective
        )
        if not isinstance(effective_objective, str) or not effective_objective.strip():
            raise WorkflowError(
                f"stage {stage.value} needs an objective in the prompt pack or caller"
            )
        prompt_context = dict(context or {})
        feedback = prompt_context.get("controller_feedback")
        if isinstance(feedback, str) and feedback:
            prompt_context["controller_feedback"] = self.render_correction(feedback)
        header: dict[str, Any] = {
            "stage": stage.value,
            "actor_role": actor_role.value,
            "objective": effective_objective,
            "context": prompt_context,
        }
        from ..orchestration.protocol import describe
        header["protocol"] = describe(stage.value)
        if prompt_context.get("repair_execution"):
            header["protocol"]["completion"] = prompt_context["repair_execution"]["completion"]
        embedded_documents = "\n\n".join(
            (f'<skill_document path="{document.relative_path}">\n'
             f"{document.content}\n</skill_document>"
             if (known_documents or {}).get(document.relative_path) != document.digest
             else f'<skill_document_unchanged path="{document.relative_path}" '
                  f'sha256="{document.digest}" '
                  f'source_path="{escape(str(self.skill_root / document.relative_path), quote=True)}" />')
            for document in documents
        )
        text = self.prompt_pack.template
        for marker, value in {
            "{{job_json}}": json.dumps(header, ensure_ascii=False, sort_keys=True, indent=2),
            "{{skill_documents}}": embedded_documents,
            "{{execution_rules}}": (
                (self.prompt_pack.root / "execution.md").read_text(encoding="utf-8")
                if "{{execution_rules}}" in text and self._executable(stage.value) else ""
            ),
        }.items():
            text = text.replace(marker, value)
        if not text.endswith("\n"):
            text += "\n"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return RenderedPrompt(
            text=text,
            digest=digest,
            objective=effective_objective,
            documents=documents,
            prompt_pack_name=self.prompt_pack.name,
            prompt_pack_manifest_digest=self.prompt_pack.manifest_digest,
            prompt_template_digest=self.prompt_pack.template_digest,
            output_schema=stage_specification.output_schema,
        )

    def render_correction(self, error: str) -> str:
        return self.prompt_pack.correction_template.replace("{{error}}", error)
