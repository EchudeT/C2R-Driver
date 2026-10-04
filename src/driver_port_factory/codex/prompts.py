from __future__ import annotations

import hashlib
from html import escape
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.contracts import StageKey
from ..core.models import ActorRole, ControllerError
from ..core.workflow import StageCatalog


@dataclass(frozen=True, slots=True)
class PromptDocument:
    relative_path: str
    digest: str
    content: str
    source_path: Path


@dataclass(frozen=True, slots=True)
class PromptStage:
    documents: tuple[str, ...]
    objective: str | None
    on_demand_documents: tuple[str, ...] = ()


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
    policy_digest: str


def default_prompt_pack_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "prompt-packs" / "default"


def _load_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    if not path.is_file():
        raise ControllerError(f"prompt pack manifest does not exist: {path}")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ControllerError(f"prompt pack manifest is not valid UTF-8 JSON: {path}") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ControllerError("prompt pack manifest must be a schema_version=1 object")
    return value, raw


def _pack_file(root: Path, value: Any, label: str) -> tuple[Path, bytes]:
    if not isinstance(value, str) or not value:
        raise ControllerError(f"prompt pack {label} must be a relative path")
    path = (root / value).resolve()
    if root not in path.parents or not path.is_file():
        raise ControllerError(f"prompt pack {label} is missing or escapes its root: {value}")
    return path, path.read_bytes()


def _prompt_stages(
    value: Any,
    catalog: StageCatalog,
    root: Path,
) -> dict[str, PromptStage]:
    if not isinstance(value, dict):
        raise ControllerError("prompt pack stages must be an object")
    stages: dict[str, PromptStage] = {}
    for stage, specification in value.items():
        if not isinstance(stage, str) or not stage:
            raise ControllerError("prompt pack stage names must be non-empty strings")
        if not catalog.contains(stage):
            raise ControllerError(f"prompt pack contains an unknown stage: {stage}")
        if not isinstance(specification, dict) or set(specification) - {"documents", "objective", "on_demand_documents"}:
            raise ControllerError(f"prompt pack stage {stage} must be a stage specification")
        documents = specification.get("documents")
        if (
            not isinstance(documents, list)
            or not documents
            or not all(isinstance(document, str) and document for document in documents)
        ):
            raise ControllerError(f"prompt pack stage {stage} needs a non-empty document list")
        objective = specification.get("objective")
        if objective is not None and (
            not isinstance(objective, str) or not objective.strip()
        ):
            raise ControllerError(
                f"prompt pack stage {stage} objective must be a non-empty string"
            )
        on_demand = specification.get("on_demand_documents", [])
        if (not isinstance(on_demand, list) or any(not isinstance(p, str) for p in on_demand)
                or not set(on_demand) <= set(documents)):
            raise ControllerError(f"on-demand documents must be selected stage documents: {stage}")
        stages[stage] = PromptStage(tuple(documents), objective, tuple(on_demand))
    return stages


def load_prompt_pack(path: Path | None, stage_catalog: StageCatalog) -> PromptPack:
    root = (path or default_prompt_pack_path()).resolve()
    manifest_path = root / "manifest.json"
    manifest, raw_manifest = _load_manifest(manifest_path)
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ControllerError("prompt pack name must be a non-empty string")
    template_path, template_raw = _pack_file(root, manifest.get("template"), "template")
    _, correction_raw = _pack_file(root, manifest.get("correction_template"), "correction template")
    try:
        template = template_raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ControllerError(f"prompt pack template is not UTF-8: {template_path}") from error
    try:
        correction_template = correction_raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ControllerError("prompt pack correction template is not UTF-8") from error
    if "{{error}}" not in correction_template:
        raise ControllerError("prompt pack correction template is missing {{error}}")
    required_markers = {"{{job_json}}", "{{skill_documents}}"}
    missing_markers = sorted(marker for marker in required_markers if marker not in template)
    if missing_markers:
        raise ControllerError(
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
            raise ControllerError(f"Skill root does not exist: {self.skill_root}")
        self.allowed_stages = frozenset(allowed_stage_names)
        self.prompt_pack = load_prompt_pack(prompt_pack, stage_catalog)

    def _read_document(self, relative_path: str) -> PromptDocument:
        path = (self.skill_root / relative_path).resolve()
        if self.skill_root not in path.parents or not path.is_file():
            # A prompt-pack-local protocol is useful for controller-owned
            # duties that are not part of the user-supplied Skill.  Keep the
            # manifest path stable while still rejecting escapes and missing
            # files; Skill documents continue to take precedence when present.
            path = (self.prompt_pack.root / relative_path).resolve()
            if self.prompt_pack.root not in path.parents:
                raise ControllerError(f"document escapes both Skill and prompt pack roots: {relative_path}")
        if not path.is_file():
            raise ControllerError(f"required prompt document is missing: {path}")
        raw = path.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ControllerError(f"Skill document is not UTF-8: {path}") from error
        return PromptDocument(
            relative_path=relative_path,
            digest=hashlib.sha256(raw).hexdigest(),
            content=content,
            source_path=path,
        )

    def documents_for_stage(self, stage: StageKey) -> tuple[PromptDocument, ...]:
        if stage.value not in self.allowed_stages:
            raise ControllerError(f"stage {stage.value} is outside the current project workflow")
        specification = self.prompt_pack.stages.get(stage.value)
        if specification is None:
            raise ControllerError(
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
        deciding = bool((context or {}).get("checker_decision"))
        recovery = ((self.prompt_pack.root / "checker-decision.md").read_text(encoding="utf-8")
                    if deciding else None)
        documents = (self.documents_for_stage(stage) if stage.value in self.prompt_pack.stages
                     else (self._read_document("open-kernel-driver-port/SKILL.md"),)
                     if deciding else self.documents_for_stage(stage))
        repair_task = None
        if (context or {}).get("repair_execution"):
            # Composite repair is an additional delivery section. Keep the
            # stage objective visible so an implementation repair does not
            # look like a packaging-only task.
            repair_task = (self.prompt_pack.root / "repair.md").read_text(encoding="utf-8")
            extra = "knowledge-guided-driver-port/references/qemu-evidence.md"
            if all(d.relative_path != extra for d in documents):
                documents = (*documents, self._read_document(extra))
        stage_specification = self.prompt_pack.stages.get(stage.value)
        effective_objective = (
            objective if objective is not None else stage_specification.objective if stage_specification else None
        )
        if repair_task is not None and objective is None:
            effective_objective = (
                "Repair the recorded defect or complete the missing delivery using current source, "
                "frozen obligations and bound observations. Preserve unaffected passing work; "
                "perform only necessary changes and affected validation under repair.md. "
                "The existing delivery scope remains required; this is not a new initial implementation.")
        if deciding and effective_objective is None:
            effective_objective = recovery
        if not isinstance(effective_objective, str) or not effective_objective.strip():
            raise ControllerError(
                f"stage {stage.value} needs an objective in the prompt pack or caller"
            )
        from .behavior_prompt import focused_context

        prompt_context = focused_context(stage, context or {})
        instructions: dict[str, Any] = {
            "stage": stage.value,
            "actor_role": actor_role.value,
            "objective": effective_objective,
            "skill_root": str(self.skill_root),
        }
        from ..orchestration.protocol import describe
        instructions["protocol"] = describe(stage.value)
        if (context or {}).get("behavior_progress"):
            instructions["objective"] = context["behavior_progress"]["objective"]
            instructions["protocol"] = {**instructions["protocol"],
                "completion": (
                    "Follow reference_material.behavior_progress. This scheduling boundary "
                    "takes precedence over the default continuous-task increment guidance. "
                    "Work on the selected complete behavior only. Prefer driver_checks.progress "
                    "with status done/continue and an optional short note; no report required. "
                    "Alternatively submit the existing report with "
                    "decision=operation and operation=behavior_done or behavior_continue. "
                    "This advances progress, not acceptance. When no behavior remains, prepare "
                    "the full delivery and submit pass through the ordinary gates."
                ),
                "operations": ["behavior_done", "behavior_continue"],
                "operation_delivery": (
                    "Submit --decision operation --operation behavior_done or behavior_continue "
                    "with the same report. Neither records stage acceptance."
                ),
            }
        if deciding and effective_objective != recovery:
            instructions["recovery"] = recovery
        if prompt_context.get("repair_execution"):
            if not prompt_context.get("behavior_progress"):
                instructions["protocol"]["completion"] = (
                    prompt_context["repair_execution"]["completion"]
                )
            instructions["delivery_repair"] = repair_task
        for key in ("tool_runtime", "phase", "repair_targets", "repair_execution"):
            if key in prompt_context:
                instructions[key] = prompt_context.pop(key)
        if prompt_context.get("controller_feedback"):
            instructions["correction"] = self.render_correction(
                "See reference_material.controller_feedback for the diagnostic."
            )
        from ..short_refs import References, compact
        runtime = instructions.get("tool_runtime", {})
        root = runtime.get("project_root")
        if root and (Path(root) / ".dpf").is_dir():
            prompt_context = compact(prompt_context, References(root))
        header = {"instructions": instructions, "reference_material": prompt_context}
        from .behavior_prompt import documents as behavior_documents
        from .behavior_prompt import template as behavior_template

        documents = behavior_documents(stage, context, documents)
        on_demand = set(stage_specification.on_demand_documents) if stage_specification else set()
        from .behavior_prompt import active as behavior_active
        if behavior_active(stage, context):
            on_demand.add("delivery-task.md")
        embedded_documents = "\n\n".join(
            (f'<skill_document_reference path="{document.relative_path}" '
             f'source_path="{escape(str(document.source_path), quote=True)}" '
             f'use="read applicable sections on demand; not assumed read" />')
            if document.relative_path in on_demand else
            (f'<skill_document path="{document.relative_path}" '
             f'source_path="{escape(str(document.source_path), quote=True)}">\n'
             f"{document.content}\n</skill_document>"
             if (known_documents or {}).get(document.relative_path) != document.digest
             else f'<skill_document_unchanged path="{document.relative_path}" '
                  f'source_path="{escape(str(document.source_path), quote=True)}" />')
            for document in documents
        )
        text = behavior_template(stage, context, self.prompt_pack)
        template_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        substitutions = {
            "{{job_json}}": json.dumps(header, ensure_ascii=False, sort_keys=True, indent=2).replace("<", "\\u003c").replace(">", "\\u003e"),
            "{{skill_documents}}": embedded_documents,
            "{{execution_rules}}": (
                self.execution_rules(stage, context)
                if "{{execution_rules}}" in text and self._executable(stage.value) else ""
            ),
            "{{review_rules}}": self.review_rules(stage),
        }
        # Substitute only the template, never placeholders appearing inside supplied content.
        text = re.sub(r"\{\{(?:job_json|skill_documents|execution_rules|review_rules)\}\}",
                      lambda match: substitutions[match.group()], text)
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
            prompt_template_digest=template_digest,
            policy_digest=self.policy_digest(stage),
        )

    def execution_rules(self, stage, context):
        if stage.value == "environment_recovery":
            return ""  # environment-task owns this stage; driver execution rules do not apply.
        progress = (context or {}).get("behavior_progress")
        focused = self.prompt_pack.root / "behavior-execution.md"
        if progress and (progress.get("current") or not progress.get("plan_exists")) and focused.is_file():
            return focused.read_text(encoding="utf-8")
        return (self.prompt_pack.root / "execution.md").read_text(encoding="utf-8")

    def review_rules(self, stage: StageKey) -> str:
        if stage.value not in {"analysis_review", "final_evidence_review"}:
            return ""
        filename = {
            "analysis_review": "review.md",
            "final_evidence_review": "final-evidence-review.md",
        }[stage.value]
        return (self.prompt_pack.root / filename).read_text(encoding="utf-8")

    def policy_digest(self, stage: StageKey) -> str:
        """Hash this stage's rules, excluding runtime context and unrelated stages."""
        from ..orchestration.protocol import describe
        specification = self.prompt_pack.stages.get(stage.value)
        documents = self.documents_for_stage(stage) if specification else ()
        recovery = self.prompt_pack.root / "checker-decision.md"
        execution_rules = self.execution_rules(stage, None) if self._executable(stage.value) else None
        repair_stages = {"driver_implementation", "artifact_preparation"}
        repair_rules = None
        if stage.value in repair_stages:
            repair_rules = (self.prompt_pack.root / "repair.md").read_text()
            extra = "knowledge-guided-driver-port/references/qemu-evidence.md"
            if all(d.relative_path != extra for d in documents):
                documents = (*documents, self._read_document(extra))
        from .optional_tools import GUIDE, STAGES
        value = {
            "optional_tools": GUIDE.read_text() if stage.value in STAGES else None,
            "composite_rules": {
                name: {"objective": self.prompt_pack.stages[name].objective,
                       "document_delivery": list(self.prompt_pack.stages[name].on_demand_documents),
                       "documents": {path: self._read_document(path).digest
                                     for path in self.prompt_pack.stages[name].documents}}
                for name in ({"target_platform_study": ("migration_contracts",),
                              "target_framework_enablement": ("driver_implementation",)}
                             .get(stage.value, ()))
            },
            "execution_rules": execution_rules,
            "behavior_execution_rules": (
                (self.prompt_pack.root / "behavior-execution.md").read_text()
                if stage.value in {"driver_implementation", "target_framework_enablement"}
                and (self.prompt_pack.root / "behavior-execution.md").is_file() else None
            ),
            "repair_rules": repair_rules,
            "document_delivery": list(specification.on_demand_documents) if specification else [],
            "objective": specification.objective if specification else None,
            "template": self.prompt_pack.template,
            "analysis_template": (
                (self.prompt_pack.root / "analysis-job.md").read_text()
                if stage.value in {"target_platform_study", "migration_contracts"}
                and (self.prompt_pack.root / "analysis-job.md").is_file() else None
            ),
            "environment_template": (
                (self.prompt_pack.root / "environment-job.md").read_text()
                if stage.value == "environment_recovery"
                and (self.prompt_pack.root / "environment-job.md").is_file() else None
            ),
            "behavior_template": (
                (self.prompt_pack.root / "behavior-job.md").read_text()
                if stage.value == "driver_implementation"
                and (self.prompt_pack.root / "behavior-job.md").is_file() else None
            ),
            "correction": self.prompt_pack.correction_template,
            "protocol": describe(stage.value),
            "documents": {d.relative_path: d.digest for d in documents},
            "recovery": recovery.read_text() if recovery.is_file() else None,
            "review_rules": self.review_rules(stage),
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def render_correction(self, error: str) -> str:
        return self.prompt_pack.correction_template.replace("{{error}}", escape(error, quote=False))
