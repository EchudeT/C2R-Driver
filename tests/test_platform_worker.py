"""Offline controller/executor tests; subprocesses are local fixtures, never paid models."""

import io
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.codex.check_mcp import respond, serve
from driver_port_factory.control.monitor import platform_output
from driver_port_factory.core.execution import CommandRunner
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.platform import dependencies, integration, service, worker
from tests.test_platform_execution import (
    IMAGE,
    IMAGE_ID,
    boot_fixture,
    build_fixture,
    platform_project,
)


def environment_job(project):
    job = str(uuid.uuid4())
    path = project.control / "codex" / f"environment_recovery-{job}.metrics.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"stage": "environment_recovery", "invocation_state": "RUNNING"}))
    return job, path


def test_environment_mcp_waits_for_completion_and_monitor_reads_logs(tmp_path):
    project = platform_project(tmp_path)
    job, path = environment_job(project)
    requests = [
        {"id": 1, "method": "tools/list"},
        {
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "platform",
                "arguments": {
                    "action": "bootstrap",
                    "image": IMAGE,
                    "accelerator": "tcg",
                    "probe": "probe.sh",
                },
            },
        },
    ]
    observed = []

    def prepare(*_args):
        from driver_port_factory.platform.activity import update

        directory = project.root / "local-process"
        update(log_root=str(directory))
        result = CommandRunner(directory).run(
            [
                sys.executable,
                "-c",
                "import time; print('local progress', flush=True); time.sleep(.1)",
            ],
            cwd=project.root,
        )
        observed.extend(platform_output(project.root, (path, {"job_id": job})))
        assert result.exit_code == 0
        return {"status": "synthetic finished", "receipt": result.stdout_path}

    outgoing = io.StringIO()
    with patch("driver_port_factory.environment.bootstrap.prepare", side_effect=prepare) as run:
        serve(project, job, io.StringIO("\n".join(map(json.dumps, requests)) + "\n"), outgoing)
    results = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert [t["name"] for t in results[0]["result"]["tools"]] == ["platform"]
    assert len(results) == 2 and not results[1]["result"]["isError"]
    assert "synthetic finished" in results[1]["result"]["content"][0]["text"]
    run.assert_called_once()
    assert any("RUNNING" in line for line in observed)
    assert any("local progress" in line for line in observed)
    assert "COMPLETED" in platform_output(project.root, (path, {"job_id": job}))[0]


def test_platform_tool_rejects_stale_job_action_and_uploaded_verdict(tmp_path):
    project = platform_project(tmp_path)
    job, path = environment_job(project)
    with patch.object(service, "build") as build:
        for value in (
            {"action": "build"},
            {"action": "build", "PASS": True},
            {"action": "bootstrap"},
            {"action": []},
        ):
            with pytest.raises(WorkflowError):
                worker.run(project, job, value)
        build.assert_not_called()
    path.write_text(json.dumps({"stage": "environment_recovery", "invocation_state": "COMPLETED"}))
    with pytest.raises(WorkflowError, match="not running"):
        worker.authorize(project, job)


@pytest.mark.parametrize("error", [WorkflowError("specific failure"), SystemExit(143)])
def test_platform_failure_or_cancellation_keeps_terminal_activity(tmp_path, error):
    project = platform_project(tmp_path)
    job, _ = environment_job(project)
    args = {"action": "bootstrap", "image": IMAGE, "accelerator": "tcg", "probe": "probe.sh"}
    with patch("driver_port_factory.environment.bootstrap.prepare", side_effect=error):
        if isinstance(error, Exception):
            value = respond(
                project,
                job,
                {
                    "method": "tools/call",
                    "params": {
                        "name": "platform",
                        "arguments": args,
                    },
                },
            )
            assert value["isError"] and "specific failure" in value["content"][0]["text"]
        else:
            with pytest.raises(SystemExit):
                worker.run(project, job, args)
    activity = json.loads((project.control / "codex" / f"{job}.platform.json").read_text())
    assert activity["status"] == ("FAILED" if isinstance(error, Exception) else "CANCELLED")


@pytest.mark.parametrize("fault", [None, "resolve-source", "resolve-fail", "build-lock"])
def test_lock_resolution_precedes_freeze_and_drift_never_publishes(tmp_path, fault):
    project = platform_project(tmp_path)
    calls = []

    def resolve(profile, worktree, directory, command, **kwargs):
        calls.append("resolve")
        assert command == ["cargo", "metadata", "--offline", "--format-version", "1"]
        assert profile["image_id"] == IMAGE_ID and kwargs["build"]
        (worktree / "Cargo.lock").write_text("resolved local package\n")
        if fault == "resolve-source":
            (worktree / "driver.rs").write_text("unexpected concurrent source edit\n")
        return CommandRunner(directory / "command").run(
            ["/bin/false" if fault == "resolve-fail" else "/bin/true"],
            cwd=worktree,
        )

    def build(profile, worktree, directory, cache_key):
        calls.append("build")
        assert (worktree / "Cargo.lock").read_text() == "resolved local package\n"
        # The resolution receipt is already present when input freezing/building happens.
        assert (directory / "dependencies/dependencies.json").exists()
        if fault == "build-lock":
            (worktree / "Cargo.lock").write_text("unexpected during build\n")
        return build_fixture(profile, worktree, directory, cache_key)

    with (
        patch.object(service, "image_identity", return_value=IMAGE_ID),
        patch.object(service.executor, "build", side_effect=build_fixture),
        patch.object(service.executor, "boot", side_effect=boot_fixture),
    ):
        service.prepare(project, IMAGE, "tcg")
        service.verify(project)
        worktree, _ = service.location(project)
        (worktree / "Cargo.toml").write_text("[workspace]\nmembers=[]\n")
        (worktree / "Cargo.lock").write_text("before\n")
        with (
            patch.object(service.executor, "container", side_effect=resolve),
            patch.object(service.executor, "build", side_effect=build),
        ):
            if fault:
                with pytest.raises(WorkflowError):
                    service.build(project)
                assert not (service.root(project) / "current-build.json").exists()
            else:
                built = service.build(project)
                assert service.presence(project)["status"] == "BUILD_IDENTITY_MATCH"
                assert Path(built["dependencies"]).is_file()
        assert calls == (
            ["resolve"] if fault in {"resolve-source", "resolve-fail"} else ["resolve", "build"]
        )
        receipt = next(service.root(project).glob("runs/*/dependencies/dependencies.json"))
        assert receipt.with_name("Cargo.lock.before").read_text() == "before\n"
        assert receipt.with_name("Cargo.lock.after").read_text() == "resolved local package\n"
        result = json.loads(receipt.read_text())
        assert result["status"] == (
            "FAIL" if fault in {"resolve-source", "resolve-fail"} else "DEPENDENCIES_RESOLVED"
        )


def test_missing_workspace_is_not_a_successful_dependency_resolution(tmp_path):
    with pytest.raises(WorkflowError, match="Cargo.toml"):
        dependencies.prepare({}, tmp_path, tmp_path / "attempt")


def test_on_demand_integration_example_uses_current_sources_and_real_reference(tmp_path):
    files = {
        "Cargo.toml": '[workspace]\nmembers=["comp", "owner"]\n'
        '[workspace.dependencies]\nmy-component={path="comp"}\n',
        "Components.toml": '[components]\nexample={name="my-component"}\n',
        "comp/Cargo.toml": '[package]\nname="my-component"\n[dependencies]\ncomponent="1"\n',
        "comp/src/lib.rs": "#[init_component]\nfn init() {}\n",
        "owner/Cargo.toml": (
            '[package]\nname="owner"\n[dependencies]\nmy-component.workspace=true\n'
        ),
        "owner/src/lib.rs": "use my_component as _;\n",
    }
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    with (
        patch.object(service, "verified"),
        patch.object(service, "load", return_value=({"target_revision": "synthetic"}, tmp_path)),
    ):
        value = integration.example(None, "my-component")
        assert value["target_revision"] == "synthetic"
        refs = value["consumers"][0]["rust_references"]
        assert refs[0]["path"] == "owner/src/lib.rs"
        assert "use my_component as _;" in refs[0]["excerpts"][0]["text"]
        (tmp_path / "owner/src/lib.rs").write_text("// Reference removed\n")
        assert integration.example(None, "my-component")["consumers"][0]["rust_references"] == []
