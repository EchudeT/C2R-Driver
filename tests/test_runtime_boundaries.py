import json
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from driver_port_factory.control.runtime import controller_run, controller_status
from driver_port_factory.core.execution import CommandRunner, script_command
from driver_port_factory.core.models import WorkflowError


@pytest.mark.parametrize("header", ["#!/usr/bin/env bash\n", "#!/bin/bash\n", ""])
def test_bash_harness_matches_authored_behavior_without_executable_bit(tmp_path, header):
    script = tmp_path / "script with spaces.sh"
    script.write_text(header + "set -euo pipefail\nitems=(one two)\n[[ ${items[1]} == two ]]\n")
    result = CommandRunner(tmp_path / "runs").run(script_command(script), cwd=tmp_path)
    assert result.exit_code == 0


def test_explicit_non_bash_interpreter_is_respected(tmp_path):
    script = tmp_path / "check.sh"
    script.write_text(f"#!{sys.executable}\nprint('interpreter respected')\n")
    result = CommandRunner(tmp_path / "runs").run(script_command(script), cwd=tmp_path)
    assert result.exit_code == 0
    assert "interpreter respected" in Path(result.stdout_path).read_text()


def test_timeout_terminates_owned_child_group(tmp_path):
    script = tmp_path / "sleep.sh"
    script.write_text("#!/bin/bash\nsleep 120 &\necho $! > child.pid\nwait\n")
    result = CommandRunner(tmp_path / "runs").run(
        script_command(script), cwd=tmp_path, timeout_seconds=1
    )
    assert result.timed_out and result.exit_code == 124
    pid = (tmp_path / "child.pid").read_text().strip()
    status = Path(f"/proc/{pid}/stat")
    assert not status.exists() or status.read_text().split(")", 1)[1].split()[0] == "Z"


def test_controller_lock_reports_liveness_and_rejects_duplicate(tmp_path):
    project = SimpleNamespace(control=tmp_path)
    assert controller_status(project)["state"] == "UNTRACKED"
    with pytest.raises(RuntimeError, match="test stop"), controller_run(project):
        assert controller_status(project)["state"] == "ACTIVE"
        with pytest.raises(WorkflowError, match="already running"), controller_run(project):
            pytest.fail("duplicate controller entered")
        raise RuntimeError("test stop")
    assert controller_status(project)["state"] == "STOPPED"
    assert "test stop" in json.loads((tmp_path / "controller.json").read_text())["error"]
    with controller_run(project):
        assert controller_status(project)["state"] == "ACTIVE"


def test_controller_sigterm_unwinds_and_restores_handler(tmp_path):
    project = SimpleNamespace(control=tmp_path)
    original = signal.getsignal(signal.SIGTERM)
    with pytest.raises(KeyboardInterrupt), controller_run(project):
        os.kill(os.getpid(), signal.SIGTERM)
    assert signal.getsignal(signal.SIGTERM) == original
    assert controller_status(project)["state"] == "STOPPED"
