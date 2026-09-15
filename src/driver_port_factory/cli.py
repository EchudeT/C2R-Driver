from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .acquisition.service import AcquisitionService
from .codex.gateway import CodexExecGateway, CodexJob, CodexSdkGateway
from .codex.prompts import SkillPromptComposer
from .core.models import ActorRole, EvaluationMode, ProjectConfig, StageStatus, WorkflowError
from .core.project import Project
from .environment.service import EnvironmentService
from .intake.service import IntakeService
from .knowledge.index import KnowledgeIndex
from .knowledge.service import DOMAINS, KnowledgeService
from .sealing.candidate import CandidateSealer
from .target_study.service import TargetStudyService


def _project(path: str) -> Project:
    return Project(Path(path))


def command_init(arguments: argparse.Namespace) -> None:
    root = Path(arguments.path).resolve()
    config = ProjectConfig(
        project_id=arguments.project_id or root.name,
        source_platform=arguments.source,
        target_platform=arguments.target,
        driver_name=arguments.driver,
        evaluation_mode=EvaluationMode(arguments.mode),
        actor_role=ActorRole(arguments.role),
        skill_root=str(Path(arguments.skill_root).resolve()) if arguments.skill_root else None,
    )
    Project.initialize(root, config)
    print(root)


def command_status(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    print(
        f"project={project.config.project_id} role={project.config.actor_role.value} "
        f"mode={project.config.evaluation_mode.value}"
    )
    for stage in project.store.stages():
        dependencies = ",".join(stage.dependencies) or "-"
        print(
            f"{stage.position + 1:02d} {stage.status.value:17} {stage.owner.value:11} "
            f"{stage.name:28} deps={dependencies}"
        )


def command_intake_analyze(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    result = IntakeService().analyze(
        project,
        raw_request=arguments.request,
        catalog_paths=tuple(Path(path).resolve() for path in (arguments.catalog or ())),
    )
    print(
        json.dumps(
            {
                "status": result.status.value,
                "selected_candidate_id": result.selected_candidate_id,
                "question": result.question,
                "candidates": [candidate.to_dict() for candidate in result.candidates],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_intake_answer(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    answer_text = arguments.answer or (
        f"Selected candidate {arguments.candidate_id}"
        if arguments.candidate_id
        else "Confirmed manually supplied driver identity"
    )
    result = IntakeService().answer(
        project,
        candidate_id=arguments.candidate_id,
        answer_text=answer_text,
        canonical_name=arguments.canonical_name,
        source_path=arguments.source_path,
        device_family=arguments.device_family,
        bus=arguments.bus,
        intended_subset=tuple(arguments.intended_subset or ()),
        excluded_variants=tuple(arguments.exclude or ()),
    )
    print(
        json.dumps(
            {
                "status": result.status.value,
                "selected_candidate_id": result.selected_candidate_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_intake_show(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    print(json.dumps(IntakeService().show(project), ensure_ascii=False, sort_keys=True, indent=2))


def command_acquire_plan(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    plan = AcquisitionService().plan(
        project,
        source_url=arguments.source_url,
        source_ref=arguments.source_ref,
        target_url=arguments.target_url,
        target_ref=arguments.target_ref,
        qemu_url=arguments.qemu_url,
        qemu_ref=arguments.qemu_ref,
        registry_path=Path(arguments.registry).resolve() if arguments.registry else None,
    )
    print(json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True, indent=2))


def command_acquire_run(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    result = AcquisitionService().acquire(project)
    print(
        json.dumps(
            {
                "status": result.status.value,
                "target_worktree": result.target_worktree,
                "source_identity_consistent": result.source_identity_consistent,
                "checkouts": [record.to_dict() for record in result.checkouts],
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_acquire_verify(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    result = AcquisitionService().verify(project)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    if not result["valid"]:
        raise WorkflowError("one or more acquired repositories failed verification")


def command_environment_inspect(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    result = EnvironmentService().inspect(project)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


def command_environment_plan(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    plan = EnvironmentService().register_plan(project, Path(arguments.file))
    print(json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True, indent=2))


def command_environment_run(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    result = EnvironmentService().run(project, arguments.route_id)
    print(
        json.dumps(
            {
                "route_id": result.route_id,
                "readiness": result.readiness.value,
                "stage_status": result.stage_status.value,
                "attempt_path": result.attempt_path,
                "message": result.message,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_knowledge_add(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    record = KnowledgeService().add_material(
        project,
        identifier=arguments.identifier,
        domain=arguments.domain,
        path=Path(arguments.file),
        source_url=arguments.source_url,
        revision=arguments.revision,
        license_note=arguments.license,
        redistribution=arguments.redistribution,
        category=arguments.category,
        authority=arguments.authority,
        original=not arguments.derived,
        index=not arguments.no_index,
        notes=arguments.notes,
    )
    print(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2))


def command_knowledge_gap(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    record = KnowledgeService().add_gap(
        project,
        identifier=arguments.identifier,
        domain=arguments.domain,
        reason=arguments.reason,
        revision=arguments.revision,
        category=arguments.category,
    )
    print(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2))


def command_knowledge_bootstrap(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    result = KnowledgeService().bootstrap(project, probe_plan_path=Path(arguments.probe_plan))
    print(
        json.dumps(
            {
                "readiness": result.readiness,
                "stage_status": result.stage_status.value,
                "generated_skill_path": result.generated_skill_path,
                "failed_probe_ids": list(result.failed_probe_ids),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_knowledge_rebuild(arguments: argparse.Namespace) -> None:
    print(
        json.dumps(
            KnowledgeIndex(Path(arguments.path)).build(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_knowledge_status(arguments: argparse.Namespace) -> None:
    print(
        json.dumps(
            KnowledgeIndex(Path(arguments.path)).status(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_knowledge_inventory(arguments: argparse.Namespace) -> None:
    print(
        json.dumps(
            KnowledgeIndex(Path(arguments.path)).inventory(domain=arguments.domain),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_knowledge_search(arguments: argparse.Namespace) -> None:
    print(
        json.dumps(
            KnowledgeIndex(Path(arguments.path)).search(
                arguments.query,
                domain=arguments.domain,
                record_id=arguments.record_id,
                path_prefix=arguments.path_prefix,
                limit=arguments.limit,
            ),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_knowledge_show(arguments: argparse.Namespace) -> None:
    print(
        json.dumps(
            KnowledgeIndex(Path(arguments.path)).show(arguments.chunk_id),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_target_study_validate(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    result = TargetStudyService().validate(
        project,
        profile_json=Path(arguments.profile_json),
        profile_markdown=Path(arguments.profile_markdown),
        api_table=Path(arguments.api_table),
        analogous_trace=Path(arguments.analogous_trace),
        change_plan=Path(arguments.change_plan),
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "stage_status": result.stage_status.value,
                "report_path": result.report_path,
                "errors": list(result.errors),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_stage_start(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    project.start(arguments.stage)
    print(f"started {arguments.stage}")


def command_stage_complete(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    project.complete(arguments.stage, StageStatus(arguments.outcome), message=arguments.message)
    print(f"{arguments.stage} -> {arguments.outcome}")


def command_artifact_add(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    digest = project.add_artifact(
        arguments.stage,
        arguments.kind,
        Path(arguments.file),
        direction=arguments.direction,
    )
    print(digest)


def _render_prompt(project: Project, stage: str, objective: str, context_path: str | None):
    if not project.config.skill_root:
        raise WorkflowError("project has no skill_root; initialize it with --skill-root")
    context = None
    if context_path:
        context = json.loads(Path(context_path).read_text(encoding="utf-8"))
        if not isinstance(context, dict):
            raise WorkflowError("Prompt context must be a JSON object")
    composer = SkillPromptComposer(Path(project.config.skill_root))
    return composer.render(
        stage=stage,
        actor_role=project.config.actor_role,
        objective=objective,
        context=context,
    )


def command_prompt_render(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    stage = project.store.stage(arguments.stage)
    if stage.status not in {StageStatus.READY, StageStatus.RUNNING}:
        raise WorkflowError(
            f"Prompt may only be rendered for a READY/RUNNING stage, got {stage.status.value}"
        )
    rendered = _render_prompt(project, arguments.stage, arguments.objective, arguments.context)
    project.add_bytes(
        arguments.stage,
        "codex_prompt",
        rendered.text.encode("utf-8"),
        source=f"generated:prompt:{rendered.digest}",
        direction="input",
    )
    if arguments.output:
        output = Path(arguments.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered.text, encoding="utf-8")
        print(json.dumps({"prompt_sha256": rendered.digest, "output": str(output)}))
    else:
        sys.stdout.write(rendered.text)


def command_codex_run(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    stage = project.store.stage(arguments.stage)
    if stage.status is StageStatus.READY:
        project.start(arguments.stage)
    elif stage.status is not StageStatus.RUNNING:
        raise WorkflowError(f"Codex stage must be READY or RUNNING, got {stage.status.value}")
    rendered = _render_prompt(project, arguments.stage, arguments.objective, arguments.context)
    project.add_bytes(
        arguments.stage,
        "codex_prompt",
        rendered.text.encode("utf-8"),
        source=f"generated:prompt:{rendered.digest}",
        direction="input",
    )
    codex_dir = project.control / "codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    output_path = (
        Path(arguments.output).resolve()
        if arguments.output
        else codex_dir / f"{arguments.stage}-{rendered.digest[:12]}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    schema = Path(arguments.schema).resolve() if arguments.schema else None
    job = CodexJob(
        stage=arguments.stage,
        actor_role=project.config.actor_role,
        objective=arguments.objective,
        prompt=rendered.text,
        workspace=project.root,
        output_schema=schema,
        output_path=output_path,
        model=arguments.model,
        sandbox=arguments.sandbox,
        thread_id=arguments.thread_id,
    )
    gateway = (
        CodexExecGateway(arguments.codex_bin) if arguments.backend == "exec" else CodexSdkGateway()
    )
    result = gateway.run(job)
    if not output_path.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.final_response, encoding="utf-8")
    if schema:
        try:
            json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise WorkflowError(
                "Codex output is not valid JSON despite an output schema"
            ) from error
    project.add_artifact(arguments.stage, arguments.result_kind, output_path)
    if result.events:
        event_data = "\n".join(json.dumps(event, sort_keys=True) for event in result.events) + "\n"
        project.add_bytes(
            arguments.stage,
            "codex_event_log",
            event_data.encode("utf-8"),
            source=f"generated:codex-job:{result.job_id}",
        )
    if arguments.complete:
        project.complete(arguments.stage, StageStatus.PASS)
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


def command_seal(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    seal = CandidateSealer().seal(
        project, output=Path(arguments.output).resolve() if arguments.output else None
    )
    print(seal.digest)


def command_ledger_verify(arguments: argparse.Namespace) -> None:
    project = _project(arguments.path)
    valid = project.store.verify_event_chain()
    print("PASS" if valid else "FAIL")
    if not valid:
        raise WorkflowError("event ledger verification failed")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="dpf", description="Driver Port Factory")
    commands = root.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="initialize a role-specific project workspace")
    init.add_argument("path")
    init.add_argument("--project-id")
    init.add_argument("--source", required=True)
    init.add_argument("--target", required=True)
    init.add_argument("--driver", required=True)
    init.add_argument("--mode", choices=[value.value for value in EvaluationMode], required=True)
    init.add_argument("--role", choices=[value.value for value in ActorRole], required=True)
    init.add_argument("--skill-root")
    init.set_defaults(handler=command_init)

    status = commands.add_parser("status", help="show the stage DAG and current status")
    status.add_argument("path")
    status.set_defaults(handler=command_status)

    intake = commands.add_parser("intake", help="analyze and freeze the requested driver scope")
    intake_commands = intake.add_subparsers(dest="intake_command", required=True)
    analyze = intake_commands.add_parser("analyze")
    analyze.add_argument("path")
    analyze.add_argument("--request", required=True)
    analyze.add_argument(
        "--catalog",
        action="append",
        help="versioned lightweight metadata catalog JSON; may be repeated",
    )
    analyze.set_defaults(handler=command_intake_analyze)
    answer = intake_commands.add_parser("answer")
    answer.add_argument("path")
    answer.add_argument("--candidate-id")
    answer.add_argument("--answer")
    answer.add_argument("--canonical-name")
    answer.add_argument("--source-path")
    answer.add_argument("--device-family")
    answer.add_argument("--bus")
    answer.add_argument("--intended-subset", action="append")
    answer.add_argument("--exclude", action="append")
    answer.set_defaults(handler=command_intake_answer)
    show = intake_commands.add_parser("show")
    show.add_argument("path")
    show.set_defaults(handler=command_intake_show)

    acquire = commands.add_parser(
        "acquire", help="pin and acquire source, target, and QEMU repositories"
    )
    acquire_commands = acquire.add_subparsers(dest="acquire_command", required=True)
    acquire_plan = acquire_commands.add_parser("plan")
    acquire_plan.add_argument("path")
    acquire_plan.add_argument("--source-url")
    acquire_plan.add_argument("--source-ref")
    acquire_plan.add_argument("--target-url")
    acquire_plan.add_argument("--target-ref")
    acquire_plan.add_argument("--qemu-url")
    acquire_plan.add_argument("--qemu-ref")
    acquire_plan.add_argument("--registry")
    acquire_plan.set_defaults(handler=command_acquire_plan)
    acquire_run = acquire_commands.add_parser("run")
    acquire_run.add_argument("path")
    acquire_run.set_defaults(handler=command_acquire_run)
    acquire_verify = acquire_commands.add_parser("verify")
    acquire_verify.add_argument("path")
    acquire_verify.set_defaults(handler=command_acquire_verify)

    environment = commands.add_parser(
        "environment", help="discover and execute an EXPERIMENT_READY route"
    )
    environment_commands = environment.add_subparsers(dest="environment_command", required=True)
    environment_inspect = environment_commands.add_parser("inspect")
    environment_inspect.add_argument("path")
    environment_inspect.set_defaults(handler=command_environment_inspect)
    environment_plan = environment_commands.add_parser("plan")
    environment_plan.add_argument("path")
    environment_plan.add_argument("--file", required=True)
    environment_plan.set_defaults(handler=command_environment_plan)
    environment_run = environment_commands.add_parser("run")
    environment_run.add_argument("path")
    environment_run.add_argument("--route-id", required=True)
    environment_run.set_defaults(handler=command_environment_run)

    knowledge = commands.add_parser(
        "knowledge", help="manage the provenance-checked local knowledge base"
    )
    knowledge_commands = knowledge.add_subparsers(dest="knowledge_command", required=True)
    knowledge_add = knowledge_commands.add_parser("add")
    knowledge_add.add_argument("path")
    knowledge_add.add_argument("--id", dest="identifier", required=True)
    knowledge_add.add_argument("--domain", choices=sorted(DOMAINS), required=True)
    knowledge_add.add_argument("--file", required=True)
    knowledge_add.add_argument("--source-url", required=True)
    knowledge_add.add_argument("--revision", required=True)
    knowledge_add.add_argument("--license", default="review-required")
    knowledge_add.add_argument(
        "--redistribution", choices=["allowed", "restricted", "unknown"], default="unknown"
    )
    knowledge_add.add_argument("--category")
    knowledge_add.add_argument("--authority")
    knowledge_add.add_argument("--derived", action="store_true")
    knowledge_add.add_argument("--no-index", action="store_true")
    knowledge_add.add_argument("--notes")
    knowledge_add.set_defaults(handler=command_knowledge_add)
    knowledge_gap = knowledge_commands.add_parser("gap")
    knowledge_gap.add_argument("path")
    knowledge_gap.add_argument("--id", dest="identifier", required=True)
    knowledge_gap.add_argument(
        "--domain",
        choices=sorted(DOMAINS),
        required=True,
    )
    knowledge_gap.add_argument("--reason", required=True)
    knowledge_gap.add_argument("--revision", required=True)
    knowledge_gap.add_argument("--category", required=True)
    knowledge_gap.set_defaults(handler=command_knowledge_gap)
    knowledge_bootstrap = knowledge_commands.add_parser("bootstrap")
    knowledge_bootstrap.add_argument("path")
    knowledge_bootstrap.add_argument("--probe-plan", required=True)
    knowledge_bootstrap.set_defaults(handler=command_knowledge_bootstrap)
    knowledge_rebuild = knowledge_commands.add_parser("rebuild")
    knowledge_rebuild.add_argument("path")
    knowledge_rebuild.set_defaults(handler=command_knowledge_rebuild)
    knowledge_status = knowledge_commands.add_parser("status")
    knowledge_status.add_argument("path")
    knowledge_status.set_defaults(handler=command_knowledge_status)
    knowledge_inventory = knowledge_commands.add_parser("inventory")
    knowledge_inventory.add_argument("path")
    knowledge_inventory.add_argument("--domain")
    knowledge_inventory.set_defaults(handler=command_knowledge_inventory)
    knowledge_search = knowledge_commands.add_parser("search")
    knowledge_search.add_argument("path")
    knowledge_search.add_argument("--query", required=True)
    knowledge_search.add_argument("--domain")
    knowledge_search.add_argument("--record-id")
    knowledge_search.add_argument("--path-prefix")
    knowledge_search.add_argument("--limit", type=int, default=10)
    knowledge_search.set_defaults(handler=command_knowledge_search)
    knowledge_show = knowledge_commands.add_parser("show")
    knowledge_show.add_argument("path")
    knowledge_show.add_argument("--chunk-id", required=True)
    knowledge_show.set_defaults(handler=command_knowledge_show)

    target_study = commands.add_parser(
        "target-study", help="validate the target profile and API evidence gate"
    )
    target_study_commands = target_study.add_subparsers(dest="target_study_command", required=True)
    target_study_validate = target_study_commands.add_parser("validate")
    target_study_validate.add_argument("path")
    target_study_validate.add_argument("--profile-json", required=True)
    target_study_validate.add_argument("--profile-markdown", required=True)
    target_study_validate.add_argument("--api-table", required=True)
    target_study_validate.add_argument("--analogous-trace", required=True)
    target_study_validate.add_argument("--change-plan", required=True)
    target_study_validate.set_defaults(handler=command_target_study_validate)

    stage = commands.add_parser("stage", help="manually drive a stage")
    stage_commands = stage.add_subparsers(dest="stage_command", required=True)
    start = stage_commands.add_parser("start")
    start.add_argument("path")
    start.add_argument("stage")
    start.set_defaults(handler=command_stage_start)
    complete = stage_commands.add_parser("complete")
    complete.add_argument("path")
    complete.add_argument("stage")
    complete.add_argument(
        "--outcome",
        required=True,
        choices=[
            StageStatus.PASS.value,
            StageStatus.FAIL.value,
            StageStatus.BLOCKED.value,
            StageStatus.INCONCLUSIVE.value,
            StageStatus.NOT_APPLICABLE.value,
        ],
    )
    complete.add_argument("--message")
    complete.set_defaults(handler=command_stage_complete)

    artifact = commands.add_parser("artifact", help="register immutable stage artifacts")
    artifact_commands = artifact.add_subparsers(dest="artifact_command", required=True)
    add = artifact_commands.add_parser("add")
    add.add_argument("path")
    add.add_argument("stage")
    add.add_argument("kind")
    add.add_argument("file")
    add.add_argument("--direction", choices=["input", "output"], default="output")
    add.set_defaults(handler=command_artifact_add)

    prompt = commands.add_parser("prompt", help="compose versioned prompts from upstream Skills")
    prompt_commands = prompt.add_subparsers(dest="prompt_command", required=True)
    render = prompt_commands.add_parser("render")
    render.add_argument("path")
    render.add_argument("stage")
    render.add_argument("--objective", required=True)
    render.add_argument("--context")
    render.add_argument("--output")
    render.set_defaults(handler=command_prompt_render)

    codex = commands.add_parser("codex", help="run a bounded Codex stage job")
    codex_commands = codex.add_subparsers(dest="codex_command", required=True)
    run = codex_commands.add_parser("run")
    run.add_argument("path")
    run.add_argument("stage")
    run.add_argument("--objective", required=True)
    run.add_argument("--context")
    run.add_argument("--schema")
    run.add_argument("--result-kind", required=True)
    run.add_argument("--output")
    run.add_argument("--backend", choices=["exec", "sdk"], default="exec")
    run.add_argument("--codex-bin", default="codex")
    run.add_argument("--model")
    run.add_argument(
        "--sandbox",
        choices=["read-only", "workspace-write", "danger-full-access"],
        default="workspace-write",
    )
    run.add_argument("--thread-id")
    run.add_argument("--complete", action="store_true")
    run.set_defaults(handler=command_codex_run)

    seal = commands.add_parser("seal", help="create the canonical candidate manifest")
    seal.add_argument("path")
    seal.add_argument("--output")
    seal.set_defaults(handler=command_seal)

    ledger = commands.add_parser("ledger", help="audit the hash-chained event log")
    ledger_commands = ledger.add_subparsers(dest="ledger_command", required=True)
    verify = ledger_commands.add_parser("verify")
    verify.add_argument("path")
    verify.set_defaults(handler=command_ledger_verify)
    return root


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = parser().parse_args(argv)
        arguments.handler(arguments)
    except (WorkflowError, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
