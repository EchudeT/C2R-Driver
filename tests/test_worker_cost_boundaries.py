"""Offline controller regressions; no model, Docker or real driver validation."""

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.cli import main
from driver_port_factory.codex.check_mcp import dispatch
from driver_port_factory.codex.cli import run_codex_stage
from driver_port_factory.codex.contracts import CodexBackend
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.core.execution import CommandRunner
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration.contracts import MigrationStage as S
from driver_port_factory.platform import formatting, guest, service
from tests.test_local_adaptation import ready
from tests.test_platform_execution import (
    IMAGE,
    IMAGE_ID,
    boot_fixture,
    build_fixture,
    platform_project,
)


def test_actual_worker_receives_platform_interface_and_progress_stays_unaccepted(tmp_path):
    project = ready(tmp_path)
    interface = {"status": "VERIFIED", "case_interface": {"steps": {"wait_event": "name"}}}

    def worker(job):
        payload = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])
        assert (
            payload["instructions"]["tool_runtime"]["platform_execution"]["case_interface"]["steps"]
            == interface["case_interface"]["steps"]
        )
        assert (
            payload["instructions"]["tool_runtime"]["platform_execution"]["tool"]
            == "driver_checks.platform"
        )
        dispatch(project, job.job_id, "progress", {"status": "continue", "note": "Synthetic gap"})
        return CodexResult(job.job_id, "", "same-session")

    with (
        patch.object(service, "context", return_value=interface),
        patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=worker),
    ):
        run_codex_stage(
            project,
            S.DRIVER_IMPLEMENTATION,
            context=None,
            backend=CodexBackend.EXEC,
            codex_bin="codex",
            model=None,
        )
    assert project.stage(S.DRIVER_IMPLEMENTATION).status.value == "RUNNING"
    project.verify_integrity()


def test_focused_template_identity_and_final_delivery_use_distinct_rules(tmp_path):
    from driver_port_factory.codex.prompts import SkillPromptComposer
    from driver_port_factory.composition import WORKFLOW_STAGE_CATALOG
    from driver_port_factory.core.models import ActorRole

    project = ready(tmp_path)
    composer = SkillPromptComposer(
        Path(project.config.skill_root), WORKFLOW_STAGE_CATALOG, project.workflow.stage_values
    )
    passage = "Unique shared obligation: release only after callbacks cease."
    progress = {
        "objective": "one behavior",
        "plan_exists": True,
        "current": {"id": "probe", "outcome": passage},
        "route_context": {"contracts": [{"id": "C1", "text": passage}]},
    }
    focused = composer.render(
        stage=S.DRIVER_IMPLEMENTATION,
        actor_role=ActorRole.DEVELOPER,
        context={"behavior_progress": progress},
    )
    assert focused.text.count(passage) == 1
    payload = json.loads(focused.text.split("<job>")[1].split("</job>")[0])
    packet = payload["reference_material"]["behavior_progress"]
    assert packet["current"]["id"] == "probe"
    assert packet["passages"][packet["current"]["outcome"]["text_ref"]] == passage
    assert 'skill_document_reference path="delivery-task.md"' in focused.text
    template = composer.prompt_pack.root / "behavior-job.md"
    assert focused.prompt_template_digest == hashlib.sha256(template.read_bytes()).hexdigest()
    final = composer.render(
        stage=S.DRIVER_IMPLEMENTATION,
        actor_role=ActorRole.DEVELOPER,
        context={"behavior_progress": {**progress, "current": None}},
    )
    assert final.prompt_template_digest == composer.prompt_pack.template_digest
    assert final.prompt_template_digest != focused.prompt_template_digest
    assert len(focused.text) < len(final.text)


def test_managed_format_cli_keeps_pinned_route_and_invalidates_changed_build(tmp_path, capsys):
    project = platform_project(tmp_path)
    calls = []

    def container(profile, worktree, directory, command, **options):
        calls.append((profile, command, options))
        if "--check" not in command:
            (worktree / "driver.rs").write_text("pub fn formatted() {}\n")
        return CommandRunner(directory / "command").run(["/bin/true"], cwd=worktree)

    with (
        patch("driver_port_factory.platform.dependencies.prepare", return_value="synthetic"),
        patch.object(service, "image_identity", return_value=IMAGE_ID),
        patch.object(service.executor, "build", side_effect=build_fixture),
        patch.object(service.executor, "boot", side_effect=boot_fixture),
        patch.object(service.executor, "container", side_effect=container),
    ):
        service.prepare(project, IMAGE, "tcg")
        service.verify(project)
        service.build(project)
        value = service.context(project)
        guest.validate_case(value["case_interface"]["example"])
        assert main([*value["format"][3:], "--package", "fixture"]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "FORMAT_OK"
        assert calls[-1][1] == ["cargo", "fmt", "--package", "fixture", "--", "--check"]
        assert calls[-1][0]["image_id"] == IMAGE_ID
        assert calls[-1][2]["build"] is True
        assert service.presence(project)["status"] == "BUILD_IDENTITY_MATCH"
        assert formatting.run(project, ["fixture"], write=True)["source_changed"]
        with pytest.raises(WorkflowError, match="identity mismatch"):
            service.presence(project)
        with (
            patch.object(service, "image_identity", return_value="different"),
            pytest.raises(WorkflowError, match="image changed"),
        ):
            formatting.run(project, ["fixture"])
        assert len(calls) == 2
        worktree, _ = service.location(project)
        assert len(list((worktree / ".dpf-output/platform/runs").glob("*/format.json"))) == 2
