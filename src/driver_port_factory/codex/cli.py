from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
import uuid
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
    utc_now,
)
from ..core.project import Project
from .contracts import CodexArtifact, CodexBackend, CodexExecEventType, CodexOutputError, ModelInvocationError
from .gateway import CodexExecGateway, CodexJob, CodexResult
from .policy import CodexExecutionPolicy
from .prompts import RenderedPrompt, SkillPromptComposer
from .sessions import compact_token_limit, input_changes, save_session, stage_session
from .accounting import estimate, latest_usage, model_settings, usage_delta
from .submission import (
    load_submission,
    receipt_path,
    submission_artifact,
    write_submission,
)


def _render_prompt(
    project: Project,
    stage: StageKey,
    objective: str | None,
    context: dict[str, object] | None,
    prompt_pack_path: str | None,
    known_documents: dict[str, str] | None = None,
    skill_root: Path | None = None,
) -> RenderedPrompt:
    from ..migration.repair_execution import active
    from ..migration.contracts import MigrationStage
    context = dict(context or {})
    if active(project, stage) or (
            stage is MigrationStage.ARTIFACT_PREPARATION and context.get("controller_validation_error")):
        context["repair_execution"] = {
            "mode": "prepare-once-controller-validates",
            "completion": "Repair, affected checks, runtime artifact and public runner ready; submit the completed report with the tool's pass decision.",
            "next": "Controller captures artifact and executes QEMU, then returns observations for worker self-check.",
        }
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
    if stage.owner is StageOwner.STATIC and not (context or {}).get("checker_decision"):
        raise WorkflowError(f"stage {stage_key.value} is statically owned and cannot run Codex")
    if stage.status is StageStatus.READY:
        project.start(stage_key)
    elif stage.status is not StageStatus.RUNNING:
        raise WorkflowError(f"Codex stage must be READY or RUNNING, got {stage.status.value}")
    grant = CodexExecutionPolicy().grant(project, stage_key)
    key, session = stage_session(project, stage_key, grant, model, backend.value)
    from .context_reset import attach_handoff
    context, thread_id = attach_handoff(project, session, context, thread_id)
    thread_id = thread_id or session.get("thread_id")
    job_id = str(uuid.uuid4())
    codex_dir = project.control / "codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    output_path = codex_dir / f"{stage_key.value}-{job_id}.result"
    submission = receipt_path(project, job_id)
    submit_parts = (
        sys.executable,
        "-m",
        "driver_port_factory.cli",
        "codex",
        "submit",
        str(project.root),
        stage_key.value,
        "--job-id",
        job_id,
        "--file",
        "<DELIVERABLE_PATH>",
        "--kind",
        "<proposal|report>",
        "--decision",
        "<submit|pass|rework|blocked|operation>",
    )
    submit_command = " ".join(shlex.quote(part) for part in submit_parts)
    submit_command += " [--operation OPERATION] [--repair-stage STAGE]"
    context = {
        **(context or {}),
        "tool_runtime": {
            "python": sys.executable,
            "workflow_cli": [sys.executable, "-m", "driver_port_factory.cli"],
            "execution_root": str(grant.execution_root),
            "sandbox": grant.sandbox.value,
            "evidence_locator": [sys.executable, "-m", "driver_port_factory.review_evidence"],
            "project_root": str(project.root),
            "stage": stage_key.value,
            "job_id": job_id,
            "submission_receipt": str(submission),
            "submission_command": submit_command,
            "submission_rule": (
                "Write the deliverable first, then invoke submission_command. The controller "
                "trusts only the tool receipt; the final chat response is informational."
            ),
        },
        "available_inputs": [d.value for d in project.workflow.spec(stage_key).dependencies],
    }
    from ..environment.contracts import EnvironmentStage, EnvironmentArtifact
    if (EnvironmentStage.RECOVERY.value in project.workflow.stage_values
            and project.stage(EnvironmentStage.RECOVERY).status is StageStatus.PASS):
        context["environment_evidence"] = {}
        for kind in (EnvironmentArtifact.INVENTORY, EnvironmentArtifact.MODE_RECORD):
            ref = project.artifact(EnvironmentStage.RECOVERY, kind)
            context["environment_evidence"][kind.value] = {
                "kind": kind.value, "digest": ref.digest,
                "path": str(project.artifacts.path_for_digest(ref.digest)),
            }
    if stage_key in CodexExecutionPolicy.DEPENDENCY_STAGES:
        context["tool_runtime"]["cargo_home"] = str(
            grant.execution_root / ".dpf-output" / "cargo-home"
        )
    if follow_up:
        context = {**(context or {}), "controller_feedback": follow_up}
    repair = project.retry_feedback(stage_key)
    if repair:
        context["repair_state"] = repair
        prior = [r for r in project.artifact_refs(stage=stage_key)
                 if r.kind == CodexArtifact.WORK_REPORT.value]
        if prior:
            ref = max(prior, key=lambda r: r.ordinal)
            context["previous_work_report"] = str(project.artifacts.path_for_digest(ref.digest))
        context["repair_scope"] = (
            "The prerequisite repair is complete. Continue with current frozen inputs; the historical "
            "reason is not an outstanding defect. Revalidate only affected downstream results."
            if repair["status"] == "RESOLVED" else
            "Repair the causal defect and recheck only affected claims. Preserve code, reports, "
            "compile commands, builds and passing tests whose inputs are unchanged. "
            "Reusing a report is allowed; do not restart the whole phase investigation."
        )
    from ..orchestration.protocol import REPAIR_TARGETS
    from ..core.phases import phase
    context["phase"] = phase(stage_key)
    context["repair_targets"] = [s.name.value for s in project.stages()
        if s.name.value in REPAIR_TARGETS and s.status is StageStatus.PASS
        and phase(s.name) == phase(stage_key)
        and stage_key.value in project.workflow.descendants(s.name)]
    known_inputs = session.get("inputs", {}) if thread_id == session.get("thread_id") else {}
    context, supplied_inputs = input_changes(context, known_inputs)
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
    job = CodexJob(
        stage=stage_key,
        actor_role=project.config.actor_role,
        objective=rendered.objective,
        prompt=prompt,
        execution_root=grant.execution_root,
        sandbox=grant.sandbox,
        model=model,
        thread_id=thread_id,
        job_id=job_id,
        compact_token_limit=compact_token_limit(project, stage_key, thread_id),
    )
    known_documents = session.get("documents", {}) if thread_id == session.get("thread_id") else {}
    documents = {
        **known_documents,
        **{doc.relative_path: doc.digest for doc in rendered.documents},
    }

    reported_usage = []
    first_event_seconds = None
    metrics = {
        "stage": stage_key.value, "job_id": job.job_id, "thread_id": thread_id,
        "started_at": utc_now(), "completed_at": None,
        "invocation_state": "RUNNING",
        "context_epoch": session.get("handoff", {}).get("epoch"),
        "controller_started_at": (
            json.loads((project.control / "controller.json").read_text()).get("started_at")
            if (project.control / "controller.json").is_file() else None
        ),
        **model_settings(model),
        "usage_baseline": latest_usage(codex_dir, thread_id),
        "resumed": bool(thread_id), "prompt_bytes": len(prompt.encode()),
        "auto_compact_token_limit": job.compact_token_limit,
        "policy_sha256": rendered.policy_digest,
        "call_reason": (
            "recovery" if context.get("checker_decision") or follow_up else
            "review_followup" if stage_key.value in {"final_evidence_review", "analysis_review"} and thread_id else
            "independent_review" if stage_key.value in {"final_evidence_review", "analysis_review"} else
            "execution_self_check" if context.get("controller_execution") else
            "repair" if repair and repair["status"] == "OPEN" else "stage_work"
        ),
        "usage_semantics": "cumulative thread counters; subtract usage_baseline",
    }

    def persist_metrics() -> None:
        metrics.update(elapsed_seconds=round(time.monotonic() - started, 3),
                       first_response_seconds=first_event_seconds, reported_usage=reported_usage)
        metrics["usage"] = usage_delta(
            reported_usage[-1] if reported_usage else None, metrics["usage_baseline"])
        metrics["estimate"] = estimate(metrics["usage"], metrics["model"], metrics["service_tier"])
        path = output_path.with_suffix(".metrics.json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(metrics))
        temporary.replace(path)

    def checkpoint(event: dict) -> None:
        nonlocal first_event_seconds
        if first_event_seconds is None and event.get("type") not in {"thread.started", "turn.started"}:
            first_event_seconds = round(time.monotonic() - started, 3)
        if event.get("type") == "turn.completed" and event.get("usage"):
            reported_usage.append(event["usage"])
        with output_path.with_suffix(".events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")
        if event.get("type") == CodexExecEventType.THREAD_STARTED.value:
            metrics["thread_id"] = event.get("thread_id")
            save_session(project, key, event.get("thread_id"), known_documents, known_inputs)
            persist_metrics()
        elif event.get("type") == "turn.completed":
            persist_metrics()

    gateway = CodexExecGateway(codex_bin, on_event=checkpoint)
    started = time.monotonic()
    persist_metrics()
    try:
        result = gateway.run(job)
        metrics["invocation_state"] = "FAILED" if result.error else "COMPLETED"
    except (WorkflowError, OSError, subprocess.SubprocessError) as error:
        metrics["invocation_state"] = "FAILED"
        raise ModelInvocationError(str(error)) from error
    finally:
        if metrics["invocation_state"] == "RUNNING":
            metrics["invocation_state"] = "INTERRUPTED"
        metrics["completed_at"] = utc_now()
        persist_metrics()
    save_session(
        project,
        key,
        result.thread_id,
        documents if not result.error else known_documents,
        supplied_inputs if not result.error else known_inputs,
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
    if result.error:
        raise ModelInvocationError(result.error)
    receipt = load_submission(project, stage_key, job.job_id)
    if receipt is None:
        raise CodexOutputError(
            "Codex did not submit a deliverable; write the file and invoke "
            "the supplied tool_runtime.submission_command"
        )
    deliverable = Path(receipt["file"])
    output_path.write_bytes(deliverable.read_bytes())
    submission_value = receipt
    job_ref = project.record_artifact(stage_key, FileArtifact(CodexArtifact.JOB_RESULT, output_path))
    if submission_value is not None and job_ref.ordinal is not None:
        project.record_artifact(
            stage_key,
            GeneratedArtifact(
                CodexArtifact.SUBMISSION,
                submission_artifact(
                    submission_value,
                    job_digest=job_ref.digest,
                    job_ordinal=job_ref.ordinal,
                ),
                f"generated:codex-submission:{job.job_id}",
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
    from ..control.runtime import controller_run
    project = open_project(Path(arguments.path))
    stage_key = project.workflow.parse_stage(arguments.stage)
    with controller_run(project):
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


def command_codex_submit(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    stage = project.workflow.parse_stage(arguments.stage)
    receipt = write_submission(
        project,
        stage,
        job_id=arguments.job_id,
        file_path=arguments.file,
        kind=arguments.kind,
        decision=arguments.decision,
        operation=arguments.operation,
        repair_stage=arguments.repair_stage,
    )
    print(f"submitted {stage.value} {arguments.kind} via {receipt}")


def command_session_reset(arguments: argparse.Namespace) -> None:
    from ..control.runtime import controller_run
    from .context_reset import reset_session
    project = open_project(Path(arguments.path))
    stage = project.workflow.parse_stage(arguments.stage)
    with controller_run(project):
        grant = CodexExecutionPolicy().grant(project, stage)
        key, _ = stage_session(project, stage, grant, arguments.model, CodexBackend.EXEC.value)
        handoff = reset_session(project, stage, key, reason=arguments.reason)
    print(json.dumps(handoff))


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

    reset = codex_commands.add_parser(
        "reset-session", help="rotate a developer conversation with a factual handoff"
    )
    reset.add_argument("path")
    reset.add_argument("stage")
    reset.add_argument("--reason", required=True)
    reset.add_argument("--model")
    reset.set_defaults(handler=command_session_reset)

    submit = codex_commands.add_parser(
        "submit", help="submit a file-backed worker deliverable and state decision"
    )
    submit.add_argument("path")
    submit.add_argument("stage")
    submit.add_argument("--job-id", required=True)
    submit.add_argument("--file", required=True)
    submit.add_argument("--kind", choices=("proposal", "report"), required=True)
    submit.add_argument(
        "--decision", choices=("submit", "pass", "rework", "blocked", "operation"), required=True
    )
    submit.add_argument("--operation")
    submit.add_argument("--repair-stage")
    submit.set_defaults(handler=command_codex_submit)

    transcript = codex_commands.add_parser(
        "transcript", help="inspect persisted Codex prompts, responses, and event logs"
    )
    transcript.add_argument("path")
    transcript.add_argument("stage")
    transcript.add_argument("--include-content", action="store_true")
    transcript.set_defaults(handler=command_codex_transcript)
