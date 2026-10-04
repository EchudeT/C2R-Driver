"""Offline protocol boundaries; real Docker repetitions live in a separate explicit script."""

import json
from unittest.mock import patch

import pytest

from driver_port_factory.environment.managed_smoke import run
from tests.test_environment_bootstrap import IMAGE_ID

pytest_plugins = ["tests.test_environment_bootstrap"]


@pytest.mark.parametrize("failure", ["collector", "cleanup", "missing_qemu", "help"])
def test_capture_failure_is_distinct_from_no_device_execution(
    tmp_path, docker_stub, monkeypatch, failure
):
    flags = {
        "collector": "FAKE_MISSING_TRACE",
        "cleanup": "FAKE_CLEANUP_FAILURE",
        "missing_qemu": "FAKE_NO_QEMU",
        "help": "FAKE_HELP_ONLY",
    }
    monkeypatch.setenv(flags[failure], "1")
    probe = tmp_path / "probe.sh"
    probe.write_text("exit 0\n")
    capture = run(
        tmp_path,
        tmp_path / "attempt",
        {
            "image": "asterinas/dev:test",
            "image_id": IMAGE_ID,
            "timeout_seconds": 2,
        },
        probe,
    )
    assert bool(capture.infrastructure_errors) == (failure in {"collector", "cleanup"})
    if failure != "cleanup":
        assert not json.loads(capture.output.read_text())["observations"]
    receipt = json.loads((tmp_path / "attempt/capture.json").read_text())
    assert receipt["commands"][-1]["argv"][1:4] == ["rm", "-f", "-v"]
    assert not any(c["argv"][1] in {"events", "top", "ps"} for c in receipt["commands"])


def test_infrastructure_error_leaves_paid_repair_loop(tmp_path):
    """The actual port loop must propagate ControllerError without requesting model recovery."""
    from types import SimpleNamespace

    from driver_port_factory.core.models import ControllerError, StageStatus
    from driver_port_factory.port import PortRunner

    stage = SimpleNamespace(name="environment", status=StageStatus.RUNNING)
    runner = object.__new__(PortRunner)
    project = SimpleNamespace(config=SimpleNamespace(), stage=lambda _: stage)
    runner._project = lambda: project
    runner._current = lambda _: stage

    def fail(_):
        raise ControllerError("collector unavailable")

    runner._actions = {"environment": fail}
    with (
        patch("driver_port_factory.core.checker_decision.pending_decision", return_value=None),
        patch("driver_port_factory.core.checker_decision.request_recovery") as retry,
        pytest.raises(ControllerError, match="collector unavailable"),
    ):
        runner._run_project(project)
    retry.assert_not_called()


def test_each_execution_captures_fresh_probe_and_never_reuses_prior_pass(tmp_path, docker_stub):
    probe = tmp_path / "probe.sh"
    recipe = {"image": "asterinas/dev:test", "image_id": IMAGE_ID, "timeout_seconds": 2}
    probe.write_text("exit 0\n")
    first = run(tmp_path, tmp_path / "first", recipe, probe)
    probe.write_text("exit 9\n")
    second = run(tmp_path, tmp_path / "second", recipe, probe)
    assert first.command.exit_code == 0
    assert second.command.exit_code == 9
    assert not second.infrastructure_errors
    assert first.output != second.output
    calls = [json.loads(line) for line in docker_stub.read_text().splitlines()]
    names = [args[args.index("--name") + 1] for args in calls if args[0] == "create"]
    assert len(names) == len(set(names)) == 2


def test_legacy_event_listener_retains_stderr(tmp_path):
    import sys

    from driver_port_factory.core.container_events import ContainerEvents

    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\nimport sys\nprint('event failure', file=sys.stderr)\nsys.exit(7)\n"
    )
    docker.chmod(0o755)
    errors = []
    diagnostics = tmp_path / "events.stderr"
    events = ContainerEvents(str(docker), "0", lambda _: None, errors.append, diagnostics)
    events.thread.join(timeout=3)
    events.close()
    assert diagnostics.read_text().strip() == "event failure"
    assert any("exited 7" in error and "event failure" in error for error in errors)
