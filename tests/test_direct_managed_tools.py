"""Offline gateway boundary checks; no model, Docker or driver execution."""

import subprocess
import tomllib
from unittest.mock import patch

import pytest

from driver_port_factory.codex.contracts import CodexSandbox
from driver_port_factory.codex.gateway import CodexExecGateway, CodexJob
from driver_port_factory.core.models import ActorRole
from driver_port_factory.migration.contracts import MigrationStage


@pytest.mark.parametrize("thread", [None, "existing-worker"])
@pytest.mark.parametrize("managed", [False, True])
def test_managed_namespace_is_direct_only_without_changing_other_tools(tmp_path, thread, managed):
    job = CodexJob(
        MigrationStage.DRIVER_IMPLEMENTATION,
        ActorRole.DEVELOPER,
        "fixture",
        "unchanged prompt",
        tmp_path,
        CodexSandbox.UNRESTRICTED,
        thread_id=thread,
        checks_project=tmp_path if managed else None,
    )
    with (
        patch("driver_port_factory.codex.gateway.relay_overrides", return_value=[]),
        patch(
            "driver_port_factory.codex.gateway.execute",
            return_value=subprocess.CompletedProcess([], 0, '{"type":"turn.completed"}\n', ""),
        ) as execute,
    ):
        CodexExecGateway().run(job)

    argv = execute.call_args.args[0]
    config = tomllib.loads("\n".join(argv[i + 1] for i, arg in enumerate(argv) if arg == "-c"))
    assert execute.call_args.kwargs["prompt"] == "unchanged prompt"
    if managed:
        assert config["features"]["code_mode"] == {
            "direct_only_tool_namespaces": ["mcp__driver_checks"]
        }
        assert config["mcp_servers"]["driver_checks"]["tool_timeout_sec"] == 86400
    else:
        assert "features" not in config
        assert "mcp_servers" not in config
