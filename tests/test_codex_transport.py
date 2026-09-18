from __future__ import annotations

import sys
import json
from subprocess import CompletedProcess
from pathlib import Path
from types import SimpleNamespace

from driver_port_factory.codex.transport import execute
from driver_port_factory.codex.gateway import CodexExecGateway, CodexJob, CodexResult
from driver_port_factory.codex.contracts import CodexSandbox
from driver_port_factory.core.models import ActorRole, EvaluationMode, WorkflowError
from driver_port_factory.codex.sessions import session_key
from driver_port_factory.migration.contracts import MigrationStage
from driver_port_factory.environment.contracts import EnvironmentStage
from unittest.mock import patch
import pytest


def test_checkpoint_is_persisted_before_process_completes(tmp_path: Path):
    marker = tmp_path / "checkpoint"
    prompt = "测试" * 100000
    script = (
        "import pathlib,sys,time\n"
        f"assert len(sys.stdin.read()) == {len(prompt)}\n"
        "sys.stderr.write('diagnostic ' * 10000)\n"
        "print('{\"type\":\"thread.started\",\"thread_id\":\"persistent\"}',flush=True)\n"
        f"marker=pathlib.Path({str(marker)!r})\n"
        "deadline=time.monotonic()+5\n"
        "while not marker.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        "assert marker.read_text()=='persistent'\n"
    )
    result = execute(
        [sys.executable, "-c", script], cwd=tmp_path, prompt=prompt,
        on_event=lambda event: marker.write_text(event["thread_id"]),
    )
    assert result.returncode == 0
    assert len(result.stderr) <= 8000


def test_concurrent_resume_is_rejected_and_lock_released(tmp_path):
    job = CodexJob(stage=MigrationStage.DRIVER_IMPLEMENTATION,
                   actor_role=ActorRole.DEVELOPER, objective="test", prompt="test",
                   execution_root=tmp_path, sandbox=CodexSandbox.WORKSPACE_WRITE,
                   thread_id=str(tmp_path))
    gateway = CodexExecGateway()

    def nested(current):
        with pytest.raises(WorkflowError, match="already running"):
            gateway.run(current)
        return CodexResult(current.job_id, "ok", current.thread_id)

    with patch.object(gateway, "_run", side_effect=nested):
        assert gateway.run(job).final_response == "ok"
        assert gateway.run(job).final_response == "ok"


@pytest.mark.parametrize("events,failed", [
    ([{"type": "item.completed", "item": {"type": "agent_message", "text": "still working"}}], True),
    ([{"type": "turn.completed"}], True),
    ([{"type": "turn.failed"}], True),
    ([{"type": "error", "message": "Reconnecting"},
      {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
      {"type": "turn.completed"}], False),
])
def test_only_completed_turn_with_response_is_accepted(tmp_path, events, failed):
    job = CodexJob(stage=MigrationStage.DRIVER_IMPLEMENTATION,
                   actor_role=ActorRole.DEVELOPER, objective="test", prompt="test",
                   execution_root=tmp_path, sandbox=CodexSandbox.WORKSPACE_WRITE)
    output = "\n".join(json.dumps(event) for event in [None, *events])
    with patch("driver_port_factory.codex.gateway.execute",
               return_value=CompletedProcess([], 0, output, "")):
        assert bool(CodexExecGateway().run(job).error) is failed


def test_environment_resume_can_download_dependencies(tmp_path):
    job = CodexJob(stage=EnvironmentStage.RECOVERY,
                   actor_role=ActorRole.DEVELOPER, objective="test", prompt="test",
                   execution_root=tmp_path, sandbox=CodexSandbox.WORKSPACE_WRITE,
                   thread_id="environment-worker")
    with patch("driver_port_factory.codex.gateway.execute",
               return_value=CompletedProcess([], 0, "", "")) as run:
        CodexExecGateway().run(job)
    command = run.call_args.args[0]
    assert "sandbox_workspace_write.network_access=true" in command
    assert 'sandbox_mode="workspace-write"' in command
    assert (tmp_path / ".dpf-output" / "cargo-home").is_dir()


def test_session_ignores_trust_updates_but_isolates_provider_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    base = 'model_provider = "relay"\n[model_providers.relay]\nbase_url = "https://example.test/v1"\n'
    config.write_text(base)
    project = SimpleNamespace(root=tmp_path, config=SimpleNamespace(
        actor_role=ActorRole.DEVELOPER, evaluation_mode=EvaluationMode.DEVELOPER_EVIDENCE))
    def key():
        return session_key(project, MigrationStage.DRIVER_IMPLEMENTATION, None, "gpt-5.6-sol", "exec")
    original = key()
    config.write_text(base + '\n[projects."/new/worktree"]\ntrust_level = "trusted"\n')
    assert key() == original
    config.write_text(base.replace("example.test", "different.test"))
    assert key() != original
