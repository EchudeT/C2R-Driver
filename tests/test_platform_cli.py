"""Real CLI/project/ledger boundaries with synthetic build/boot; offline, no paid models."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.cli import main
from driver_port_factory.composition import open_project
from driver_port_factory.core.models import StageStatus
from driver_port_factory.environment import smoke_recipe
from driver_port_factory.environment.contracts import EnvironmentArtifact as A
from driver_port_factory.environment.contracts import EnvironmentStage as S
from driver_port_factory.platform import service
from tests.test_platform_execution import (
    IMAGE,
    IMAGE_ID,
    boot_fixture,
    build_fixture,
    platform_project,
)


def test_cli_platform_lifecycle_preserves_failed_verification_and_acceptance_boundary(
    tmp_path, capsys
):
    project = platform_project(tmp_path)

    def invoke(action, *arguments, expected=0):
        assert main(["environment", "platform", action, str(project.root), *arguments]) == expected
        output = capsys.readouterr()
        return json.loads(output.out) if expected == 0 else output.err

    with (
        patch("driver_port_factory.platform.dependencies.prepare", return_value="synthetic"),
        patch.object(service, "image_identity", return_value=IMAGE_ID),
        patch.object(service.executor, "build", side_effect=build_fixture),
        patch.object(service.executor, "boot", side_effect=boot_fixture),
    ):
        prepared = invoke("prepare", "--image", IMAGE, "--accelerator", "tcg")
        assert prepared["status"] == "PREPARED_NOT_VERIFIED"
        assert invoke("status")["status"] == "NOT_VERIFIED"
        with patch.object(service.executor, "boot", return_value={"status": "FAIL"}):
            assert "Platform validation failed" in invoke("verify", expected=2)
        assert invoke("status")["status"] == "NOT_VERIFIED"
        assert invoke("verify")["status"] == "BASELINE_BUILD_BOOT_VERIFIED"
        assert invoke("status")["status"] == "VERIFIED"
        assert invoke("build")["status"] == "PASS"
        assert invoke("presence")["status"] == "BUILD_IDENTITY_MATCH"
        worktree, _ = service.location(project)
        case = worktree / ".dpf-output/harness/example.json"
        case.write_text('{"steps":[{"wait_event":"EXAMPLE"}]}')
        assert invoke("run-case", str(case))["status"] == "CASE_OBSERVED"
        (worktree / "driver.rs").write_text("pub fn changed() {}\n")
        assert "identity mismatch" in invoke("presence", expected=2)
        assert "changed" in invoke("run-case", str(case), expected=2)

    reopened = open_project(project.root)
    # Infrastructure observations never finalize environment or certify driver behavior.
    assert reopened.stage(S.RECOVERY).status is StageStatus.RUNNING
    validations = [
        json.loads(reopened.artifacts.read(ref))["status"]
        for ref in reopened.current_artifact_refs(stage=S.RECOVERY)
        if ref.kind == A.PLATFORM_VALIDATION.value
    ]
    assert validations == ["FAIL", "PASS"]


def test_subprocess_prepare_smoke_and_asset_cli_use_real_project_open(tmp_path, monkeypatch):
    project = platform_project(tmp_path)
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "assert sys.argv[1:3] == ['image', 'inspect'], 'No real Docker execution permitted'\n"
        f"print({IMAGE_ID!r})\n"
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))

    def invoke(*arguments):
        result = subprocess.run(
            [sys.executable, "-m", "driver_port_factory.cli", *map(str, arguments)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            cwd=project.root,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    platform = invoke(
        "environment",
        "platform",
        "prepare",
        project.root,
        "--image",
        IMAGE,
        "--accelerator",
        "tcg",
    )
    assert platform["status"] == "PREPARED_NOT_VERIFIED"
    assert invoke("environment", "platform", "status", project.root)["status"] == "NOT_VERIFIED"
    work = project.root / "work/stage-work/environment_recovery"
    work.mkdir(parents=True)
    probe = work / "probe.sh"
    probe.write_text("#!/bin/sh\necho synthetic probe, not driver evidence\n")
    prepared = invoke(
        "environment", "prepare-smoke", project.root, "--image", IMAGE, "--probe", probe
    )
    assert prepared["status"] == "PREPARED_NOT_EXECUTED"
    assert smoke_recipe.inputs(project, Path(prepared["script"]))["image_id"] == IMAGE_ID
    exported = invoke(
        "platform-asset",
        "export",
        project.root,
        "--facts",
        probe,
        "--store",
        tmp_path / "assets",
        "--configuration",
        "fixture",
    )
    imported = invoke(
        "platform-asset",
        "import",
        project.root,
        "--asset",
        exported["asset"],
        "--configuration",
        "fixture",
    )
    assert imported["status"] == "EVIDENCE_ONLY"
    assert invoke("platform-asset", "status", project.root)[0]["digest"] == exported["sha256"]
    assert open_project(project.root).stage(S.RECOVERY).status is StageStatus.RUNNING


@pytest.mark.parametrize("command", ["platform", "prepare-smoke", "platform-asset"])
def test_mutating_cli_rejects_corrupt_cas_before_any_execution(tmp_path, capsys, command):
    project = platform_project(tmp_path)
    ref = next(iter(project.current_artifact_refs(stage=S.RECOVERY)))
    project.artifacts.path_for_digest(ref.digest).write_bytes(b"corrupted frozen evidence")
    arguments = {
        "platform": [
            "environment",
            "platform",
            "prepare",
            str(project.root),
            "--image",
            IMAGE,
            "--accelerator",
            "tcg",
        ],
        "prepare-smoke": [
            "environment",
            "prepare-smoke",
            str(project.root),
            "--image",
            IMAGE,
            "--probe",
            "probe.sh",
        ],
        "platform-asset": [
            "platform-asset",
            "import",
            str(project.root),
            "--asset",
            str(tmp_path / "missing.json"),
            "--configuration",
            "fixture",
        ],
    }[command]
    with (
        patch.object(service, "prepare") as platform,
        patch.object(smoke_recipe, "prepare") as smoke,
        patch("driver_port_factory.platform_assets.import_asset") as asset,
    ):
        assert main(arguments) == 2
        error = capsys.readouterr().err
        assert "scoped integrity" not in error
        assert "artifact" in error.lower()
        platform.assert_not_called()
        smoke.assert_not_called()
        asset.assert_not_called()
