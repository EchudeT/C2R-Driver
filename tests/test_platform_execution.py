"""Offline regression fixtures for infrastructure; no model, Docker or driver claims."""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.core.models import WorkflowError
from driver_port_factory.environment.contracts import EnvironmentStage as S
from driver_port_factory.environment.inventory import EnvironmentInspector
from driver_port_factory.platform import guest, service
from driver_port_factory.platform.profile import asterinas, container_argv

IMAGE = "asterinas/dev:fixture"
IMAGE_ID = "sha256:" + "a" * 64


def fake_qemu(tmp_path, mode="normal"):
    script = tmp_path / "qemu-system-fixture"
    script.write_text(
        f"#!{sys.executable}\nMODE={mode!r}\n"
        + r"""
import json,os,socket,sys,time
from pathlib import Path
Path(__file__).with_suffix('.pid').write_text(str(os.getpid()))
if MODE=='exit':
    print('synthetic launch failure',file=sys.stderr)
    sys.exit(3)
address=sys.argv[sys.argv.index('-qmp')+1].split(':',1)[1].split(',')[0]
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
s.bind(address)
s.listen(1)
c,_=s.accept()
f=c.makefile('rwb',buffering=0)
f.write(b'{"QMP":')
time.sleep(.02)
f.write(b'{} }\n'.replace(b'\\n',b'\n'))
for raw in f:
    req=json.loads(raw)
    if MODE=='stall':
        time.sleep(10)
    f.write((json.dumps({'return':{},'id':req['id']})+'\n').encode())
    if req['execute']=='cont':
        print('/ # ',end='',flush=True)
        f.write(b'{"event":"READY"}\n')
"""
    )
    script.chmod(0o755)
    return script


@pytest.mark.parametrize("mode", ["normal", "exit", "stall"])
def test_transport_bounds_qmp_preserves_logs_and_cleans_process(tmp_path, mode):
    qemu = fake_qemu(tmp_path, mode)
    directory = tmp_path / ("deep" * 40)
    result = guest.run(
        {"qemu": str(qemu), "qemu_args": []},
        "synthetic.iso",
        {"timeout_seconds": 1, "steps": [{"wait_serial": "/ # "}, {"wait_event": "READY"}]},
        directory,
    )
    assert (result["status"] == "PASS") is (mode == "normal")
    assert (directory / "qemu-stderr.log").is_file()
    assert (directory / "qmp.jsonl").is_file()
    pid = int(qemu.with_suffix(".pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    argv = json.loads((directory / "argv.json").read_text())
    socket_path = argv[-1].split(":", 1)[1].split(",")[0]
    assert len(socket_path) < 100 and not Path(socket_path).exists()


def test_container_route_does_not_mask_tools_or_switch_accelerator(tmp_path):
    profile = asterinas(IMAGE, IMAGE_ID, "revision", "kvm")
    argv = container_argv(
        profile, tmp_path, "name", ["make", "kernel"], build=True, cache_key="fixture"
    )
    assert "--pull=never" in argv
    assert "type=volume,source=dpf-fixture-cargo,target=/root/.cargo" in argv
    assert "host" == argv[argv.index("--network") + 1]
    boot = container_argv(
        profile,
        tmp_path,
        "name",
        ["python3", "guest.py"],
        build=False,
        cache_key="fixture",
        artifact=Path("/elsewhere/image"),
    )
    assert "none" == boot[boot.index("--network") + 1]
    assert boot[boot.index("--device") + 1] == "/dev/kvm"
    assert "type=bind,source=/elsewhere/image,target=/elsewhere/image,readonly" in boot
    with pytest.raises(ValueError, match="Unknown"):
        guest.validate_case({"steps": [{"make_up_success": True}]})


def platform_project(tmp_path):
    from driver_port_factory.acquisition.repository import RepositoryAcquirer
    from tests.acquisition_support import close_evidence
    from tests.repository_support import project_config, ready_project

    config = replace(project_config(), target_platform="asterinas")
    files = {
        p: "synthetic fixture\n"
        for p in ("Makefile", "OSDK.toml", "rust-toolchain.toml", "tools/qemu_args.sh")
    }
    files[".gitignore"] = "target/\n"
    with patch("tests.repository_support.project_config", return_value=config):
        project, *_ = ready_project(tmp_path, target_overrides=files)
    RepositoryAcquirer().acquire(project)
    close_evidence(project, {})
    with patch.object(EnvironmentInspector, "_local_probes", return_value=[]):
        EnvironmentInspector().inspect(project)
    return project


def build_fixture(profile, worktree, directory, cache_key):
    directory.mkdir(parents=True, exist_ok=True)
    artifact = worktree / profile["artifact"]
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"synthetic ISO")
    (directory / "build.json").write_text('{"status":"PASS"}')
    from driver_port_factory.knowledge.index import file_sha256
    from driver_port_factory.platform.profile import digest

    return {
        "status": "PASS",
        "artifact": str(artifact),
        "profile": digest(profile),
        "artifact_sha256": file_sha256(artifact),
    }


def boot_fixture(profile, worktree, directory, artifact, case, cache_key):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "serial.log").write_text("Synthetic guest output; not a real kernel")
    return {"status": "PASS"}


def test_platform_receipt_is_required_stale_evidence_and_failed_retry_do_not_pass(tmp_path):
    project = platform_project(tmp_path)
    with patch.object(service, "image_identity", return_value=IMAGE_ID):
        service.prepare(project, IMAGE, "tcg")
        assert service.route_errors(project, {"image_ids": [IMAGE_ID]}) == []
        assert service.route_errors(project, {"image_ids": ["different-image"]})
        with pytest.raises(WorkflowError, match="Missing"):
            service.verified(project)
        with (
            patch.object(service.executor, "build", side_effect=build_fixture),
            patch.object(service.executor, "boot", side_effect=boot_fixture),
        ):
            service.verify(project)
            assert service.verified(project)["status"] == "PASS"
            from driver_port_factory.migration.experiments import execution_policy

            assert execution_policy(project)["images"] == [IMAGE]
            assert execution_policy(project)["required"] is True
            receipt = service.verified(project)
            log = project.root / receipt["evidence"][0]["path"]
            log.write_text("tampered")
            with pytest.raises(WorkflowError, match="changed"):
                service.verified(project)
            with (
                patch.object(service.executor, "boot", return_value={"status": "FAIL"}),
                pytest.raises(WorkflowError, match="failed"),
            ):
                service.verify(project)
            with pytest.raises(WorkflowError, match="no current passing"):
                service.verified(project)
    assert project.stage(S.RECOVERY).status.value == "RUNNING"
    project.verify_integrity()


def test_delivery_rejects_source_artifact_image_drift_and_failed_build(tmp_path, monkeypatch):
    project = platform_project(tmp_path)
    with (
        patch("driver_port_factory.platform.dependencies.prepare", return_value="synthetic"),
        patch.object(service, "image_identity", return_value=IMAGE_ID),
        patch.object(service.executor, "build", side_effect=build_fixture),
        patch.object(service.executor, "boot", side_effect=boot_fixture),
    ):
        service.prepare(project, IMAGE, "tcg")
        service.verify(project)
        service.build(project)
        assert service.presence(project)["status"] == "BUILD_IDENTITY_MATCH"
        worktree, _ = service.location(project)
        case = worktree / ".dpf-output/harness/example.json"
        case.write_text('{"steps":[{"wait_event":"EXAMPLE"}]}')
        assert service.run_case(project, case)["status"] == "CASE_OBSERVED"
        source = worktree / "driver.rs"
        source.write_text("pub fn changed() {}\n")
        with pytest.raises(WorkflowError, match="identity mismatch"):
            service.presence(project)
        with pytest.raises(WorkflowError, match="changed"):
            service.run_case(project, case)
        service.build(project)
        runtime = worktree / ".dpf-output/runtime-artifact"
        runtime.write_bytes(b"different runtime")
        with pytest.raises(WorkflowError, match="identity mismatch"):
            service.presence(project)
        service.build(project)
        with (
            patch.object(service, "image_identity", return_value="sha256:" + "b" * 64),
            pytest.raises(WorkflowError, match="image changed"),
        ):
            service.run_case(project, case)
        with (
            patch.object(service.executor, "build", side_effect=WorkflowError("build failed")),
            pytest.raises(WorkflowError, match="build failed"),
        ):
            service.build(project)
        assert not (service.root(project) / "current-build.json").exists()
    project.verify_integrity()


def test_environment_cannot_accept_device_smoke_without_platform_validation(tmp_path):
    from driver_port_factory.environment.execution import ExperimentExecutor

    project = platform_project(tmp_path)
    script = project.root / "environment-smoke.sh"
    script.write_text("exit 0\n")
    report = project.root / "report.md"
    report.write_text("Device model works; this cannot certify a target boot.\n")
    with pytest.raises(WorkflowError, match="Missing platform evidence"):
        ExperimentExecutor().run_codex_harness(project, script_path=script, work_report_path=report)
    assert project.stage(S.RECOVERY).status.value == "RUNNING"


def test_container_cancellation_still_removes_only_its_owned_container(tmp_path):
    from driver_port_factory.platform import executor

    profile = asterinas(IMAGE, IMAGE_ID, "revision", "tcg")
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "run":
            raise SystemExit(143)

    with (
        patch.object(executor.CommandRunner, "run", side_effect=execute),
        pytest.raises(SystemExit),
    ):
        executor.container(
            profile,
            tmp_path,
            tmp_path / "attempt",
            ["sleep", "10"],
            build=False,
            cache_key="fixture",
            timeout=2,
        )
    assert calls[1] == ["docker", "rm", "-f", calls[0][calls[0].index("--name") + 1]]


def test_two_expected_events_cannot_reuse_one_observation(tmp_path):
    qemu = fake_qemu(tmp_path)
    result = guest.run(
        {"qemu": str(qemu), "qemu_args": []},
        "synthetic.iso",
        {"timeout_seconds": 1, "steps": [{"wait_event": "READY"}, {"wait_event": "READY"}]},
        tmp_path / "logs",
    )
    assert result["status"] == "FAIL"
    assert "TIMEOUT: EVENT READY" in result["error"]


def test_container_kvm_is_not_rejected_for_host_user_permissions(tmp_path):
    project = platform_project(tmp_path)
    with (
        patch.object(service, "image_identity", return_value=IMAGE_ID),
        patch.object(service.os, "access", return_value=False),
        patch.object(Path, "is_char_device", return_value=True),
    ):
        receipt = service.prepare(project, IMAGE, "kvm")
        assert receipt["status"] == "PREPARED_NOT_VERIFIED"
        assert service.load(project)[0]["accelerator"] == "kvm"
        with pytest.raises(WorkflowError, match="Missing"):
            service.verified(project)
    with (
        patch.object(Path, "is_char_device", return_value=False),
        pytest.raises(WorkflowError, match="missing; no TCG fallback"),
    ):
        service.prepare(project, IMAGE, "kvm")
