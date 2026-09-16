from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..composition import WORKFLOW_STAGE_CATALOG, open_project
from ..core.contracts import StageKey
from ..core.models import (
    ArtifactDirection,
    FileArtifact,
    GeneratedArtifact,
    StageOwner,
    StageStatus,
    WorkflowError,
)
from ..core.project import Project
from .contracts import CodexArtifact, CodexBackend
from .gateway import CodexExecGateway, CodexJob, CodexResult, CodexSdkGateway
from .policy import CodexExecutionPolicy
from .prompts import RenderedPrompt, SkillPromptComposer


def _render_prompt(
    project: Project,
    stage: StageKey,
    objective: str,
    context: dict[str, object] | None,
    prompt_pack_path: str | None,
) -> RenderedPrompt:
    if not project.config.skill_root:
        raise WorkflowError("project has no skill_root; initialize it with --skill-root")
    configured_pack = prompt_pack_path or project.config.prompt_pack
    composer = SkillPromptComposer(
        Path(project.config.skill_root),
        WORKFLOW_STAGE_CATALOG,
        project.workflow.stage_values,
        Path(configured_pack) if configured_pack else None,
    )
    return composer.render(
        stage=stage,
        actor_role=project.config.actor_role,
        objective=objective,
        context=context,
    )


def _render_correction(
    project: Project,
    error: str,
    prompt_pack_path: str | None,
) -> str:
    configured_pack = prompt_pack_path or project.config.prompt_pack
    composer = SkillPromptComposer(
        Path(project.config.skill_root or ""),
        WORKFLOW_STAGE_CATALOG,
        project.workflow.stage_values,
        Path(configured_pack) if configured_pack else None,
    )
    return composer.render_correction(error)


def _load_context(project: Project, path: str | None) -> dict[str, object] | None:
    if not path:
        return None
    controlled = CodexExecutionPolicy.controlled_input(project, path, "Prompt context")
    context = json.loads(controlled.read_text(encoding="utf-8"))
    if not isinstance(context, dict):
        raise WorkflowError("Prompt context must be a JSON object")
    return context


def run_codex_stage(
    project: Project,
    stage_key: StageKey,
    *,
    objective: str,
    context: dict[str, object] | None,
    backend: CodexBackend,
    codex_bin: str,
    model: str | None,
    prompt_pack_path: str | None = None,
    thread_id: str | None = None,
    follow_up: str | None = None,
) -> tuple[CodexResult, RenderedPrompt, Path]:
    stage = project.stage(stage_key)
    if stage.owner is StageOwner.STATIC:
        raise WorkflowError(f"stage {stage_key.value} is statically owned and cannot run Codex")
    if stage.status is StageStatus.READY:
        project.start(stage_key)
    elif stage.status is not StageStatus.RUNNING:
        raise WorkflowError(f"Codex stage must be READY or RUNNING, got {stage.status.value}")
    rendered = _render_prompt(
        project,
        stage_key,
        objective,
        context,
        prompt_pack_path,
    )
    prompt = (
        _render_correction(project, follow_up, prompt_pack_path)
        if follow_up is not None
        else rendered.text
    )
    project.record_artifact(
        stage_key,
        GeneratedArtifact(
            CodexArtifact.PROMPT,
            prompt.encode("utf-8"),
            f"generated:prompt:{rendered.digest}" if follow_up is None else "generated:follow-up",
        ),
        direction=ArtifactDirection.INPUT,
    )
    codex_dir = project.control / "codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    grant = CodexExecutionPolicy().grant(project, stage_key)
    job = CodexJob(
        stage=stage_key,
        actor_role=project.config.actor_role,
        objective=objective,
        prompt=prompt,
        execution_root=grant.execution_root,
        sandbox=grant.sandbox,
        output_schema=(rendered.output_schema.path if rendered.output_schema else None),
        model=model,
        thread_id=thread_id,
    )
    output_path = codex_dir / f"{stage_key.value}-{job.job_id}.result"
    gateway = CodexExecGateway(codex_bin) if backend is CodexBackend.EXEC else CodexSdkGateway()
    result = gateway.run(job)
    output_path.write_text(result.final_response, encoding="utf-8")
    if rendered.output_schema:
        try:
            json.loads(result.final_response)
        except json.JSONDecodeError as error:
            raise WorkflowError(
                "Codex output is not valid JSON despite an output schema"
            ) from error
    project.record_artifact(
        stage_key,
        FileArtifact(CodexArtifact.JOB_RESULT, output_path),
    )
    if result.events:
        event_data = "\n".join(json.dumps(event, sort_keys=True) for event in result.events) + "\n"
        project.record_artifact(
            stage_key,
            GeneratedArtifact(
                CodexArtifact.EVENT_LOG,
                event_data.encode("utf-8"),
                f"generated:codex-job:{result.job_id}",
            ),
        )
    return result, rendered, output_path


def command_prompt_render(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    stage_key = project.workflow.parse_stage(arguments.stage)
    stage = project.stage(stage_key)
    if stage.status not in {StageStatus.READY, StageStatus.RUNNING}:
        raise WorkflowError(
            f"Prompt may only be rendered for a READY/RUNNING stage, got {stage.status.value}"
        )
    rendered = _render_prompt(
        project,
        stage_key,
        arguments.objective,
        _load_context(project, arguments.context),
        arguments.prompt_pack,
    )
    project.record_artifact(
        stage_key,
        GeneratedArtifact(
            CodexArtifact.PROMPT,
            rendered.text.encode("utf-8"),
            f"generated:prompt:{rendered.digest}",
        ),
        direction=ArtifactDirection.INPUT,
    )
    if arguments.output:
        output = Path(arguments.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered.text, encoding="utf-8")
        print(json.dumps({"prompt_sha256": rendered.digest, "output": str(output)}))
    else:
        sys.stdout.write(rendered.text)


def command_codex_run(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    stage_key = project.workflow.parse_stage(arguments.stage)
    result, rendered, output_path = run_codex_stage(
        project,
        stage_key,
        objective=arguments.objective,
        context=_load_context(project, arguments.context),
        backend=arguments.backend,
        codex_bin=arguments.codex_bin,
        model=arguments.model,
        prompt_pack_path=arguments.prompt_pack,
    )
    print(
        json.dumps(
            {
                "job_id": result.job_id,
                "thread_id": result.thread_id,
                "prompt_sha256": rendered.digest,
                "output": str(output_path),
            },
            ensure_ascii=False,
        )
    )


def register_commands(commands: CommandRegistry) -> None:
    prompt = commands.add_parser("prompt", help="compose versioned prompts from upstream Skills")
    prompt_commands = command_registry(prompt, dest="prompt_command")
    render = prompt_commands.add_parser("render")
    render.add_argument("path")
    render.add_argument("stage")
    render.add_argument("--objective", required=True)
    render.add_argument("--context")
    render.add_argument("--prompt-pack", help="override the project's prompt pack for this job")
    render.add_argument("--output")
    render.set_defaults(handler=command_prompt_render)

    codex = commands.add_parser("codex", help="run a bounded Codex stage job")
    codex_commands = command_registry(codex, dest="codex_command")
    run = codex_commands.add_parser("run")
    run.add_argument("path")
    run.add_argument("stage")
    run.add_argument("--objective", required=True)
    run.add_argument("--context")
    run.add_argument("--prompt-pack", help="override the project's prompt pack for this job")
    run.add_argument(
        "--backend", type=CodexBackend, choices=list(CodexBackend), default=CodexBackend.EXEC
    )
    run.add_argument("--codex-bin", default="codex")
    run.add_argument("--model")
    run.set_defaults(handler=command_codex_run)
