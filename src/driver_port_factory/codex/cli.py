from __future__ import annotations

import argparse
import json
import sys
import time
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
from .contracts import CodexArtifact, CodexBackend, CodexExecEventType
from .gateway import CodexExecGateway, CodexJob, CodexResult
from .policy import CodexExecutionPolicy
from .prompts import RenderedPrompt, SkillPromptComposer
from .sessions import save_session, stage_session


def _render_prompt(
    project: Project,
    stage: StageKey,
    objective: str | None,
    context: dict[str, object] | None,
    prompt_pack_path: str | None,
    known_documents: dict[str, str] | None = None,
    skill_root: Path | None = None,
) -> RenderedPrompt:
    if not project.config.skill_root:
        raise WorkflowError("project has no skill_root; initialize it with --skill-root")
    configured_pack = prompt_pack_path or project.config.prompt_pack
    composer = SkillPromptComposer(
        skill_root or Path(project.config.skill_root),
        WORKFLOW_STAGE_CATALOG,
        project.workflow.stage_values,
        Path(configured_pack) if configured_pack else None,
    )
    return composer.render(
        stage=stage,
        actor_role=project.config.actor_role,
        objective=objective,
        context=context,
        known_documents=known_documents,
    )


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
    objective: str | None = None,
    context: dict[str, object] | None,
    backend: CodexBackend,
    codex_bin: str,
    model: str | None,
    prompt_pack_path: str | None = None,
    thread_id: str | None = None,
    follow_up: str | None = None,
    skill_root: Path | None = None,
) -> tuple[CodexResult, RenderedPrompt, Path]:
    stage = project.stage(stage_key)
    if stage.owner is StageOwner.STATIC:
        raise WorkflowError(f"stage {stage_key.value} is statically owned and cannot run Codex")
    if stage.status is StageStatus.READY:
        project.start(stage_key)
    elif stage.status is not StageStatus.RUNNING:
        raise WorkflowError(f"Codex stage must be READY or RUNNING, got {stage.status.value}")
    grant = CodexExecutionPolicy().grant(project, stage_key)
    key, session = stage_session(project, stage_key, grant, model, backend.value)
    thread_id = thread_id or session.get("thread_id")
    context = {
        **(context or {}),
        "tool_runtime": {
            "python": sys.executable,
            "workflow_cli": [sys.executable, "-m", "driver_port_factory.cli"],
            "execution_root": str(grant.execution_root),
            "sandbox": grant.sandbox.value,
        },
    }
    if stage_key in CodexExecutionPolicy.DEPENDENCY_STAGES:
        context["tool_runtime"]["cargo_home"] = str(
            grant.execution_root / ".dpf-output" / "cargo-home"
        )
    if follow_up:
        context = {**(context or {}), "controller_feedback": follow_up}
    rendered = _render_prompt(
        project,
        stage_key,
        objective,
        context,
        prompt_pack_path,
        session.get("documents") if thread_id == session.get("thread_id") else None,
        skill_root,
    )
    prompt = rendered.text
    project.record_artifact(
        stage_key,
        GeneratedArtifact(
            CodexArtifact.PROMPT,
            prompt.encode("utf-8"),
            f"generated:prompt:{rendered.digest}",
        ),
        direction=ArtifactDirection.INPUT,
    )
    codex_dir = project.control / "codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    job = CodexJob(
        stage=stage_key,
        actor_role=project.config.actor_role,
        objective=rendered.objective,
        prompt=prompt,
        execution_root=grant.execution_root,
        sandbox=grant.sandbox,
        output_schema=(rendered.output_schema.path if rendered.output_schema else None),
        model=model,
        thread_id=thread_id,
    )
    output_path = codex_dir / f"{stage_key.value}-{job.job_id}.result"
    known_documents = session.get("documents", {}) if thread_id == session.get("thread_id") else {}
    documents = {
        **known_documents,
        **{doc.relative_path: doc.digest for doc in rendered.documents},
    }

    reported_usage = []
    first_event_seconds = None

    def checkpoint(event: dict) -> None:
        nonlocal first_event_seconds
        if first_event_seconds is None and event.get("type") not in {"thread.started", "turn.started"}:
            first_event_seconds = round(time.monotonic() - started, 3)
        if event.get("type") == "turn.completed" and event.get("usage"):
            reported_usage.append(event["usage"])
        with output_path.with_suffix(".events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")
        if event.get("type") == CodexExecEventType.THREAD_STARTED.value:
            save_session(project, key, event.get("thread_id"), known_documents)

    gateway = CodexExecGateway(codex_bin, on_event=checkpoint)
    started = time.monotonic()
    try:
        result = gateway.run(job)
    finally:
        # Keep a record even if transport fails; CLI token counters may be cumulative,
        # so retain the reported usage rather than summing it into a fictitious bill.
        output_path.with_suffix(".metrics.json").write_text(json.dumps({
            "stage": stage_key.value, "job_id": job.job_id,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "resumed": bool(thread_id), "prompt_bytes": len(prompt.encode()),
            "first_response_seconds": first_event_seconds,
            "reported_usage": reported_usage,
            "usage_semantics": "raw CLI counters; may include resumed history; not billing totals",
        }))
    save_session(
        project,
        key,
        result.thread_id,
        documents if not result.error else known_documents,
    )
    output_path.write_text(result.final_response, encoding="utf-8")
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
    if result.error:
        raise WorkflowError(result.error)
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


def command_codex_transcript(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    stage = project.workflow.parse_stage(arguments.stage)
    artifacts = []
    codex_kinds = set(CodexArtifact)
    for direction in ArtifactDirection:
        for reference in project.artifact_refs(stage=stage, direction=direction):
            if reference.kind not in codex_kinds:
                continue
            record = {
                "direction": direction.value,
                **reference.to_dict(),
                "path": str(project.artifacts.path_for_digest(reference.digest)),
            }
            data = project.artifacts.read(reference)
            if reference.kind == CodexArtifact.EVENT_LOG:
                for line in data.decode("utf-8").splitlines():
                    event = json.loads(line)
                    if event.get("type") == CodexExecEventType.THREAD_STARTED:
                        record["thread_id"] = event.get("thread_id")
                        break
            if arguments.include_content:
                record["content"] = data.decode("utf-8")
            artifacts.append(record)
    print(json.dumps({"stage": stage.value, "artifacts": artifacts}, ensure_ascii=False, indent=2))


def register_commands(commands: CommandRegistry) -> None:
    prompt = commands.add_parser("prompt", help="compose versioned prompts from upstream Skills")
    prompt_commands = command_registry(prompt, dest="prompt_command")
    render = prompt_commands.add_parser("render")
    render.add_argument("path")
    render.add_argument("stage")
    render.add_argument("--objective")
    render.add_argument("--context")
    render.add_argument("--prompt-pack", help="override the project's prompt pack for this job")
    render.add_argument("--output")
    render.set_defaults(handler=command_prompt_render)

    codex = commands.add_parser("codex", help="run a bounded Codex stage job")
    codex_commands = command_registry(codex, dest="codex_command")
    run = codex_commands.add_parser("run")
    run.add_argument("path")
    run.add_argument("stage")
    run.add_argument("--objective")
    run.add_argument("--context")
    run.add_argument("--prompt-pack", help="override the project's prompt pack for this job")
    run.add_argument(
        "--backend", type=CodexBackend, choices=list(CodexBackend), default=CodexBackend.EXEC
    )
    run.add_argument("--codex-bin", default="codex")
    run.add_argument("--model")
    run.set_defaults(handler=command_codex_run)

    transcript = codex_commands.add_parser(
        "transcript", help="inspect persisted Codex prompts, responses, and event logs"
    )
    transcript.add_argument("path")
    transcript.add_argument("stage")
    transcript.add_argument("--include-content", action="store_true")
    transcript.set_defaults(handler=command_codex_transcript)
