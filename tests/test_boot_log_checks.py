"""Local transport/controller fixtures only; no paid models or real QEMU/driver claims."""

import json
import selectors
import subprocess
import sys
from unittest.mock import patch

import pytest

from driver_port_factory.core.models import WorkflowError
from driver_port_factory.platform import guest, log_checks, service, worker
from tests.test_platform_execution import (
    IMAGE,
    IMAGE_ID,
    boot_fixture,
    build_fixture,
    platform_project,
)


def local_guest(tmp_path):
    program = (
        "import sys,subprocess\n"
        "print('pvpanic: \\x1b[39mclaimed device\\x1b[0m\\n/# ',flush=True)\n"
        "for line in sys.stdin:\n"
        " print(line, end='', flush=True)\n"
        " subprocess.run(line, shell=True, check=False)\n"
    )
    value = guest.Guest([], tmp_path, timeout=3, ready_text="/# ")
    value.process = subprocess.Popen(
        [sys.executable, "-u", "-c", program],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    value.serial_log = (tmp_path / "serial.log").open("wb", buffering=0)
    value.logs = [value.serial_log]
    value.selector.register(value.process.stdout, selectors.EVENT_READ, "serial")
    return value


def test_boot_log_colors_pass_but_missing_message_and_command_output_cannot_pass(tmp_path):
    value = local_guest(tmp_path)
    try:
        value.steps([{"assert_boot_log": "pvpanic: claimed device"}])
        with pytest.raises(RuntimeError, match="BOOT_LOG_NOT_FOUND"):
            value.steps([{"assert_boot_log": "missing boot message"}])
        value.steps([{"guest_assert": "printf 'injected marker\\n'"}])
        assert b"injected marker" in value.serial
        with pytest.raises(RuntimeError, match="BOOT_LOG_NOT_FOUND"):
            value.steps([{"assert_boot_log": "injected marker"}])
    finally:
        value.__exit__()
    boot = (tmp_path / "boot-serial.log").read_bytes()
    assert b"\x1b[39m" in boot  # Raw evidence retains colors.
    assert b"injected marker" not in boot
    assert b"injected marker" in (tmp_path / "serial.log").read_bytes()


def test_guest_failure_returns_actual_output_without_manual_log_search(tmp_path):
    value = local_guest(tmp_path)
    try:
        value.steps([{"assert_boot_log": "claimed device"}])
        command = "printf 'dmesg: klogctl: Function not implemented\\n' >&2; exit 1"
        with pytest.raises(RuntimeError, match="Function not implemented"):
            value.steps([{"guest_assert": command}])
        observation = json.loads((tmp_path / "guest-assertions.jsonl").read_text())
        assert observation["exit_code"] == 1
        assert "Function not implemented" in observation["output_excerpt"]
        assert observation["raw_log"] == str(tmp_path / "serial.log")
    finally:
        value.__exit__()


def test_color_matching_handles_split_escape_without_erasing_other_terminal_output(tmp_path):
    value = guest.Guest([], tmp_path, timeout=1)
    value.boot_serial.extend(b"driver: \x1b[3")
    assert "driver: ready" not in guest.log_text(value.boot_serial)
    value.boot_serial.extend(b"9mready\x1b[0m")
    value.assert_boot_log("driver: ready")
    assert "a\x1b[2Kb" == guest.log_text(b"a\x1b[2Kb")
    value.__exit__()


def test_failed_run_log_recheck_reuses_frozen_capture_not_edited_files_or_changed_code(tmp_path):
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
        worktree, _ = service.location(project)
        case = worktree / ".dpf-output/harness/example.json"
        case.write_text(
            json.dumps(
                {"devices": ["synthetic-device"], "steps": [{"wait_serial": "bad raw color match"}]}
            )
        )
        directories = []

        def failed_boot(profile, root, directory, artifact, spec, cache):
            directory.mkdir(parents=True, exist_ok=True)
            directories.append(directory)
            (directory / "boot-serial.log").write_bytes(b"driver: \x1b[39mready\x1b[0m\n/# ")
            result = {"status": "FAIL", "error": "synthetic raw matching timeout"}
            (directory / "boot.json").write_text(json.dumps(result))
            return result

        with patch.object(service.executor, "boot", side_effect=failed_boot) as boot:
            with pytest.raises(WorkflowError, match="capture=T1"):
                service.run_case(project, case)
            raw = directories[0] / "boot-serial.log"
            original_receipt = (directories[0] / "boot.json").read_bytes()
            raw.write_text("fabricated success")  # Cannot alter the controller's saved observation.
            result = log_checks.check(project, "T1", ["driver: ready"])
            assert result["status"] == "BOOT_LOG_OBSERVED"
            assert result["original_run_status"] == "FAIL"
            assert result["devices"] == ["synthetic-device"]
            assert (
                log_checks.check(project, "T1", ["fabricated success"])["status"]
                == "BOOT_LOG_MISSING"
            )
            assert (directories[0] / "boot.json").read_bytes() == original_receipt
            boot.assert_called_once()  # Rechecks never boot/build.
            with pytest.raises(WorkflowError, match="Unknown controller"):
                log_checks.check(project, "T99", ["driver: ready"])
            # Source drift invalidates use as current evidence, even if the fake build emits
            # identical artifact bytes; changing the code cannot inherit the old log check.
            (worktree / "driver.rs").write_text("changed source\n")
            with pytest.raises(WorkflowError, match="identity mismatch"):
                log_checks.check(project, "T1", ["driver: ready"])
            service.build(project)
            with pytest.raises(WorkflowError, match="earlier code"):
                log_checks.check(project, "T1", ["driver: ready"])
        project.verify_integrity()


def test_worker_exposes_log_recheck_without_accepting_an_uploaded_verdict(tmp_path):
    assert worker.arguments({"action": "check_boot_log", "capture": "T1", "contains": ["ready"]})
    with pytest.raises(WorkflowError, match="Unsupported"):
        worker.arguments(
            {"action": "check_boot_log", "capture": "T1", "contains": ["ready"], "status": "PASS"}
        )


def test_boot_log_archive_failure_still_cleans_guest_process(tmp_path):
    from pathlib import Path

    value = local_guest(tmp_path)
    value.steps([{"assert_boot_log": "claimed device"}])
    original = Path.write_bytes

    def fail_archive(path, data):
        if path.name == "boot-serial.log":
            raise OSError("synthetic disk full")
        return original(path, data)

    with patch.object(Path, "write_bytes", fail_archive), pytest.raises(OSError, match="disk full"):
        value.__exit__()
    assert value.process.poll() is not None
    assert value.serial_log.closed
