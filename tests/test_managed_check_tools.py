"""Managed tool tests use synthetic QEMU and no model/network calls."""

import io
import json
import uuid
from unittest.mock import patch

import pytest

from driver_port_factory.codex.check_mcp import serve
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration.check_tools import check
from driver_port_factory.migration.contracts import MigrationStage as S
from tests.migration_support import smoke_fixture
from tests.test_translation_acceleration import active_project


def active_job(project):
    job = str(uuid.uuid4())
    directory = project.control / "codex"
    directory.mkdir(exist_ok=True)
    (directory / f"driver_implementation-{job}.metrics.json").write_text(
        json.dumps({"stage": S.DRIVER_IMPLEMENTATION.value, "invocation_state": "RUNNING"})
    )
    return job


def test_pass_is_retained_after_changes_even_when_fresh_is_requested(tmp_path):
    project, output = active_project(tmp_path)
    worktree = output.parent
    (worktree / "driver.rs").write_text("// synthetic\n")
    smoke_fixture(worktree)
    job = active_job(project)
    args = {"script": ".dpf-output/implementation-smoke.sh", "timeout": 300}
    first = check(project, job, args)
    assert "PASS; reused=False" in first
    assert "stdout:" in first and "observation:" in first
    assert "PASS; reused=True" in check(project, job, args)
    script = output / "implementation-smoke.sh"
    script.write_text(script.read_text() + "exit 1\n")
    (worktree / "driver.rs").write_text("// later source version\n")
    (output / "runtime-artifact").write_text("later artifact")
    with (
        patch.dict("os.environ", {"PATH": "/different/session"}),
        patch("driver_port_factory.migration.public_qemu._run_public_harness") as execute,
        patch(
            "driver_port_factory.migration.experiments.identity",
            side_effect=AssertionError("reuse gate"),
        ),
    ):
        result = check(project, job, {**args, "fresh": True})
        assert "PASS; reused=True" in result
        assert "Retained earlier PASS" in result
        execute.assert_not_called()
    assert project.stage(S.DRIVER_IMPLEMENTATION).status.value == "RUNNING"


def test_development_check_does_not_generate_runtime_acceptance(tmp_path):
    project, output = active_project(tmp_path)
    script = output / "build.sh"
    script.write_text("#!/bin/sh\nprintf development\n")
    job = active_job(project)
    result = check(project, job, {"level": "development", "script": ".dpf-output/build.sh"})
    assert "COMMAND_OK" in result and "no runtime" in result
    assert not (project.control / "experiment-reviews" / f"{job}.seen.json").exists()


def test_invalid_case_selection_and_uploaded_pass_never_execute(tmp_path):
    project, _ = active_project(tmp_path)
    job = active_job(project)
    with patch("driver_port_factory.migration.experiments.execute") as execute:
        for args in ({"cases": ["invented"]}, {"status": "PASS"}, {"timeout": True}):
            with pytest.raises(WorkflowError):
                check(project, job, args)
        execute.assert_not_called()


def test_mcp_protocol_returns_inline_result_and_error_without_polling():
    incoming = io.StringIO(
        "\n".join(
            json.dumps(v)
            for v in [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "check", "arguments": {}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "upload-pass"},
                },
            ]
        )
        + "\n"
    )
    outgoing = io.StringIO()
    with patch("driver_port_factory.migration.check_tools.check", return_value="result and logs"):
        serve(None, "job", incoming, outgoing)
    values = [json.loads(line)["result"] for line in outgoing.getvalue().splitlines()]
    assert values[0]["tools"][0]["name"] == "check"
    assert values[1]["content"][0]["text"] == "result and logs"
    assert values[2]["isError"]


def test_gateway_registers_per_job_tool_for_fresh_and_resumed_sessions(tmp_path):
    import subprocess
    import tomllib

    from driver_port_factory.codex.contracts import CodexSandbox
    from driver_port_factory.codex.gateway import CodexExecGateway, CodexJob
    from driver_port_factory.core.models import ActorRole

    for thread in (None, "existing-worker"):
        job = CodexJob(
            S.DRIVER_IMPLEMENTATION,
            ActorRole.DEVELOPER,
            "objective",
            "prompt",
            tmp_path,
            CodexSandbox.UNRESTRICTED,
            thread_id=thread,
            checks_project=tmp_path,
        )
        with patch(
            "driver_port_factory.codex.gateway.execute",
            return_value=subprocess.CompletedProcess([], 0, '{"type":"turn.completed"}\n', ""),
        ) as execute:
            CodexExecGateway().run(job)
        argv = execute.call_args.args[0]
        config = tomllib.loads("\n".join(a for a in argv if a.startswith("mcp_servers.")))
        server = config["mcp_servers"]["driver_checks"]
        assert server["args"][-2:] == [str(tmp_path), job.job_id]
        assert server["tool_timeout_sec"] == 86400
        assert "PYTHONPATH" in server["env"]


def test_mcp_termination_reaps_the_owned_running_development_command(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    project, output = active_project(tmp_path)
    job = active_job(project)
    pidfile = output / "child.pid"
    script = output / "slow.py"
    script.write_text(
        "#!/usr/bin/env python3\nimport os,time\nfrom pathlib import Path\n"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n"
    )
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    process = subprocess.Popen(
        [sys.executable, "-m", "driver_port_factory.codex.check_mcp", str(project.root), job],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": source_root},
    )
    try:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "check",
                "arguments": {"level": "development", "script": ".dpf-output/slow.py"},
            },
        }
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 10
        while not pidfile.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.02)
        assert pidfile.exists()
        child = int(pidfile.read_text())
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=10)
        assert not Path(f"/proc/{child}").exists()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def test_compact_feedback_retains_failure_and_full_raw_receipt(tmp_path):
    project, output = active_project(tmp_path)
    script = output / "fail.sh"
    script.write_text("#!/bin/sh\nprintf 'concrete compiler failure' >&2\nexit 2\n")
    job = active_job(project)
    result = check(project, job, {"level": "development", "script": ".dpf-output/fail.sh"})
    assert "COMMAND_FAILED" in result and "concrete compiler failure" in result
    receipt = next((project.control / "probes").glob("*/receipt.json"))
    raw = json.loads(receipt.read_text())
    assert raw["feedback"][0]["outputs"][1]["tail"] == "concrete compiler failure"
    assert raw["identity"] and raw["commands"][0]["command"]["exit_code"] == 2
