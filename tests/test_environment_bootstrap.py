"""Offline shell/executor regressions; Docker and device observations below are synthetic."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from driver_port_factory.core.models import EvaluationMode, StageStatus, WorkflowError
from driver_port_factory.environment import smoke_recipe
from driver_port_factory.environment.smoke_template import render

IMAGE_ID = "sha256:" + "a" * 64


@pytest.fixture
def docker_stub(tmp_path, monkeypatch):
    binary = tmp_path / "bin"
    binary.mkdir()
    executable = binary / "docker"
    from tests.managed_smoke_support import DOCKER_STUB

    executable.write_text(f"#!{sys.executable}\n" + DOCKER_STUB)
    executable.chmod(0o755)
    calls = tmp_path / "docker-calls.jsonl"
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_DOCKER_CALLS", str(calls))
    monkeypatch.setenv("FAKE_IMAGE_ID", IMAGE_ID)
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(tmp_path / "docker-state.json"))
    return calls


@pytest.mark.parametrize(
    "probe_text,expected", [("echo checked", 0), ("exit 9", 9), ("sleep 5", 124)]
)
def test_generated_wrapper_mounts_stdin_logs_and_preserves_failure(
    tmp_path, docker_stub, probe_text, expected
):
    root = tmp_path / "workspace with spaces"
    root.mkdir()
    (root / "probe.sh").write_text(probe_text)
    script = root / "environment-smoke.sh"
    script.write_text(render(root, "asterinas/dev:test", IMAGE_ID, "probe.sh", 1))
    result = subprocess.run(
        ["/bin/sh", str(script)], capture_output=True, text=True, timeout=15, check=False
    )
    assert result.returncode == expected
    calls = [json.loads(line) for line in docker_stub.read_text().splitlines()]
    run = next(args for args in calls if args[0] == "create")
    assert run[run.index("-v") + 1] == f"{root}:{root}"
    assert run[run.index("-w") + 1] == str(root)
    assert "--pull=never" in run
    assert not any(args[0] in {"events", "top", "ps"} for args in calls)
    assert calls[-1][:2] == ["rm", "-f"]
    assert len(list((root / ".dpf-output/environment-runs").glob("*/capture.json"))) == 1


def test_wrapper_rejects_changed_image_without_running_or_pulling(
    tmp_path, docker_stub, monkeypatch
):
    monkeypatch.setenv("FAKE_IMAGE_ID", "sha256:" + "b" * 64)
    (tmp_path / "probe.sh").write_text("exit 0\n")
    script = tmp_path / "smoke.sh"
    script.write_text(render(tmp_path, "asterinas/dev:test", IMAGE_ID, "probe.sh", 1))
    result = subprocess.run(
        ["/bin/sh", str(script)], capture_output=True, text=True, timeout=15, check=False
    )
    assert result.returncode == 125
    calls = [json.loads(line) for line in docker_stub.read_text().splitlines()]
    assert [args[0] for args in calls] == ["image", "rm"]


def test_recipe_reuse_requires_current_revision_image_and_probe(tmp_path, docker_stub):
    def project(name):
        root = tmp_path / name
        work = root / "work/stage-work/environment_recovery"
        work.mkdir(parents=True)
        (work / "probe.sh").write_text("echo synthetic check")
        return SimpleNamespace(
            root=root,
            control=root / ".dpf",
            config=SimpleNamespace(
                target_platform="asterinas",
                evaluation_mode=EvaluationMode.DEVELOPER_EVIDENCE,
                managed_platform=False,
            ),
            ensure_role=lambda *args: None,
            stage=lambda _: SimpleNamespace(status=StageStatus.RUNNING),
        )

    first, second = project("first"), project("second")
    identity = {"target": "asterinas", "repositories": {"target": "one", "qemu": "two"}}
    with patch.object(smoke_recipe, "binding", return_value=identity):
        prepared = smoke_recipe.prepare(first, Path("probe.sh"), image="asterinas/dev:test")
        reused = smoke_recipe.prepare(second, Path("probe.sh"), recipe=prepared["recipe"])
        assert reused["status"] == "PREPARED_NOT_EXECUTED"
        assert str(second.root) in Path(reused["script"]).read_text()
        before = smoke_recipe.inputs(second, Path(reused["script"]))
        Path(reused["probe"]).write_text("exit 8")
        assert smoke_recipe.inputs(second, Path(reused["script"])) != before
        with (
            patch.object(smoke_recipe, "image_identity", return_value="sha256:" + "b" * 64),
            pytest.raises(WorkflowError, match="image changed"),
        ):
            smoke_recipe.prepare(second, Path("probe.sh"), recipe=prepared["recipe"])
    with (
        patch.object(smoke_recipe, "binding", return_value={"different": "revision"}),
        pytest.raises(WorkflowError, match="revision"),
    ):
        smoke_recipe.prepare(second, Path("probe.sh"), recipe=prepared["recipe"])
    assert all(json.loads(line)[0] == "image" for line in docker_stub.read_text().splitlines())


def test_failed_environment_preserves_diagnostic_attempt_without_finalizing(tmp_path):
    from driver_port_factory.environment.contracts import EnvironmentArtifact as A
    from driver_port_factory.environment.contracts import EnvironmentStage as S
    from driver_port_factory.environment.execution import ExperimentExecutor
    from driver_port_factory.environment.inventory import EnvironmentInspector
    from tests.test_evidence_reuse import ready

    project, *_ = ready(tmp_path)
    with patch.object(EnvironmentInspector, "_local_probes", return_value=[]):
        EnvironmentInspector().inspect(project)
    root = project.root / "work/stage-work/environment_recovery"
    root.mkdir(parents=True)
    script = root / "environment-smoke.sh"
    script.write_text("exit 0\n")
    report = root / "report.md"
    report.write_text("Synthetic harness, no device validation.")

    class Trace:
        def __init__(self, workspace, output):
            self.output = output

        def __enter__(self):
            self.output.write_text(
                json.dumps(
                    {
                        "observations": [],
                        "errors": ["docker run did not bind-mount the current execution workspace"],
                    }
                )
            )
            return self

        def __exit__(self, *args):
            pass

        def executions(self, path):
            return ()

    def observed(script, trace):
        trace.write_text("")
        trace.with_suffix(".collector.json").write_text('{"available": true}')
        return ["/bin/sh", str(script)]

    with (
        patch("driver_port_factory.environment.execution.ContainerTrace", Trace),
        patch("driver_port_factory.environment.execution.observed_script_command", observed),
    ):
        result = ExperimentExecutor().run_codex_harness(
            project, script_path=script, work_report_path=report
        )
    assert result.readiness.value == "FAIL"
    assert "did not bind-mount" in result.message
    assert project.stage(S.RECOVERY).status is StageStatus.RUNNING
    attempt = json.loads(Path(result.attempt_path).read_text())
    assert attempt["failure_reasons"] and attempt["command"]["exit_code"] == 0
    refs = project.artifact_refs(stage=S.RECOVERY)
    assert any(r.kind == A.RECOVERY_ATTEMPT.value for r in refs)
    assert not any(r.kind == A.EXPERIMENT_READY_RUN.value for r in refs)


@pytest.mark.parametrize("change", [None, "probe", "image"])
def test_recipe_acceptance_binds_actual_image_and_preserves_original_probe(
    tmp_path, docker_stub, change, monkeypatch
):
    from driver_port_factory.environment.contracts import EnvironmentStage as S
    from driver_port_factory.environment.execution import ExperimentExecutor
    from driver_port_factory.environment.inventory import EnvironmentInspector
    from tests.test_evidence_reuse import ready

    project, *_ = ready(tmp_path)
    with patch.object(EnvironmentInspector, "_local_probes", return_value=[]):
        EnvironmentInspector().inspect(project)
    root = project.root / "work/stage-work/environment_recovery"
    root.mkdir(parents=True)
    original = "echo changed > probe.sh" if change == "probe" else "echo synthetic assertion"
    (root / "probe.sh").write_text(original)
    prepared = smoke_recipe.prepare(project, Path("probe.sh"), image="asterinas/dev:test")
    report = root / "report.md"
    report.write_text("Synthetic observation fixture, not a working driver.")

    if change == "image":
        from driver_port_factory.core.models import ControllerError

        monkeypatch.setenv("FAKE_CREATED_IMAGE", "sha256:" + "b" * 64)
        with pytest.raises(ControllerError, match="infrastructure failed"):
            ExperimentExecutor().run_codex_harness(
                project, script_path=Path(prepared["script"]), work_report_path=report
            )
        assert project.stage(S.RECOVERY).status is StageStatus.RUNNING
        attempts = list((project.control / "environment/codex-harness").glob("*/attempt.json"))
        assert json.loads(attempts[0].read_text())["failure_class"] == "INFRASTRUCTURE"
        return
    result = ExperimentExecutor().run_codex_harness(
        project, script_path=Path(prepared["script"]), work_report_path=report
    )
    attempt = Path(result.attempt_path)
    assert (attempt.parent / "probe.sh").read_text() == original
    assert json.loads(attempt.read_text())["repair_inputs"]["container_recipe"]["probe_sha256"]
    assert result.readiness.value == ("PASS" if change is None else "FAIL")
    assert project.stage(S.RECOVERY).status is (
        StageStatus.PASS if change is None else StageStatus.RUNNING
    )


def test_bootstrap_reuses_current_baseline_but_rejects_changed_route_or_stale_evidence(tmp_path):
    from driver_port_factory.environment.bootstrap import prepare
    from driver_port_factory.environment.contracts import EnvironmentStage
    from driver_port_factory.platform import service
    from tests.test_platform_execution import (
        IMAGE,
        IMAGE_ID,
        boot_fixture,
        build_fixture,
        platform_project,
    )

    project = platform_project(tmp_path)
    work = project.root / "work/stage-work/environment_recovery"
    work.mkdir(parents=True)
    probe = work / "probe.sh"
    probe.write_text("echo synthetic probe\n")
    with (
        patch.object(service, "image_identity", return_value=IMAGE_ID),
        patch.object(smoke_recipe, "image_identity", return_value=IMAGE_ID),
        patch.object(service.executor, "build", side_effect=build_fixture) as build,
        patch.object(service.executor, "boot", side_effect=boot_fixture) as boot,
    ):
        result = prepare(project, IMAGE, "tcg")
        assert result["status"] == "BASELINE_VERIFIED"
        assert "NOT_RUN" in Path(result["report"]).read_text()
        assert project.stage(EnvironmentStage.RECOVERY).status is StageStatus.RUNNING
        probe.write_text("echo corrected synthetic probe\n")
        prepare(project, IMAGE, "tcg")
        assert build.call_count == boot.call_count == 1
        with pytest.raises(WorkflowError, match="replace the selected route"):
            prepare(project, IMAGE + "-other", "tcg")
        receipt = service.verified(project)
        (project.root / receipt["evidence"][0]["path"]).write_text("changed")
        with pytest.raises(WorkflowError, match="changed or disappeared"):
            prepare(project, IMAGE, "tcg")
        assert build.call_count == 1


def test_managed_environment_accepts_current_baseline_without_device_probe(tmp_path):
    from driver_port_factory.environment.bootstrap import accept, prepare
    from driver_port_factory.environment.contracts import EnvironmentArtifact as A
    from driver_port_factory.environment.contracts import EnvironmentStage as S
    from driver_port_factory.platform import service
    from tests.test_platform_execution import IMAGE, boot_fixture, build_fixture, platform_project

    project = platform_project(tmp_path)
    with (
        patch.object(service, "image_identity", return_value=IMAGE_ID),
        patch.object(service.executor, "build", side_effect=build_fixture) as build,
        patch.object(service.executor, "boot", side_effect=boot_fixture) as boot,
        patch.object(smoke_recipe, "prepare") as probe,
    ):
        prepare(project, IMAGE, "tcg")
        accept(project)
        assert project.stage(S.RECOVERY).status is StageStatus.PASS
        assert build.call_count == boot.call_count == 1
        probe.assert_not_called()
        route = project.load_json_artifact(S.RECOVERY, A.EXPERIMENT_ROUTE)
        assert route["migrated_driver_runtime_ready"] is False
        assert "no device verdict" in route["scope"]
        project.verify_integrity()
