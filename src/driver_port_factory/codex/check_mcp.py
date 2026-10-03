"""Stdio MCP bridge. All stdout is protocol; checks block inside the tool."""

import json
import signal
import sys

from ..composition import open_project
from ..core.models import WorkflowError
from ..migration import check_tools

MAX_REQUEST = 65536


def dispatch(project, job_id, name, arguments):
    if name == "platform":
        from ..platform.worker import run

        return run(project, job_id, arguments)
    if name == "check":
        return check_tools.check(project, job_id, arguments)
    if name == "plan":
        from ..migration.behavior import propose

        if not isinstance(arguments, dict) or set(arguments) != {"behaviors"}:
            raise WorkflowError("Plan tool accepts behaviors only; no completion or PASS")
        return propose(project, job_id, arguments["behaviors"])
    if name == "progress":
        from ..migration.progress_tool import submit

        return submit(project, job_id, arguments)
    if name == "debug":
        from ..migration.debugging import run

        return run(project, job_id, arguments)
    raise WorkflowError("Unknown managed tool")


def respond(project, job_id, message):
    method = message.get("method")
    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "dpf-checks", "version": "1"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        from ..platform.worker import tool as platform_tool

        if project is not None:
            from ..platform.worker import authorize

            try:
                stage = authorize(project, job_id)
            except WorkflowError:
                stage = None
            if stage in {"environment_recovery", "target_platform_study", "migration_contracts"}:
                return {"tools": [platform_tool()]}
        tools = check_tools.tools()
        if project is not None and stage is not None:
            tools.append(platform_tool())
        from ..migration.debugging import tool

        tools.append(tool())
        if project is not None and project.config.behavior_scheduling:
            from ..migration.progress_tool import tool as progress_tool

            tools.append(progress_tool())
            tools.append(
                {
                    "name": "plan",
                    "description": "Propose or revise coarse observable behaviors; "
                    "the controller selects this round. "
                    "No completion is recorded here. Keep framework adaptations, test stimuli and "
                    "lifecycle/cleanup with the behavior needing them; no standalone enablement "
                    "or platform survey items. Continue the selected behavior in the same round. "
                    "Each row has id, outcome, contracts (IDs), depends_on (IDs), "
                    "constraints (strings). "
                    "Mutual dependencies merge. Source and valid unrelated progress are preserved.",
                    "inputSchema": {
                        "type": "object",
                        "required": ["behaviors"],
                        "additionalProperties": False,
                        "properties": {"behaviors": {"type": "array", "items": {"type": "object"}}},
                    },
                }
            )
        return {"tools": tools}
    if method == "tools/call":
        try:
            params = message.get("params", {})
            value = dispatch(project, job_id, params.get("name"), params.get("arguments", {}))
            return {"content": [{"type": "text", "text": value}], "isError": False}
        except (WorkflowError, ValueError, TypeError, OSError) as error:
            return {"content": [{"type": "text", "text": str(error)}], "isError": True}
    raise ValueError("Unknown MCP method")


def serve(project, job_id, incoming, outgoing):
    while line := incoming.readline(MAX_REQUEST + 1):
        message = None
        try:
            if len(line) > MAX_REQUEST:
                raise ValueError("MCP request exceeds size limit")
            message = json.loads(line)
            if not isinstance(message, dict):
                raise TypeError("MCP request must be an object")
            if "id" not in message:
                continue
            result = respond(project, job_id, message)
            response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        except (ValueError, TypeError) as error:
            response = {
                "jsonrpc": "2.0",
                "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": -32600, "message": str(error)},
            }
        outgoing.write(json.dumps(response, ensure_ascii=False) + "\n")
        outgoing.flush()


def _cancel(signum, frame):
    # Unwinds CommandRunner's BaseException handler, killing its owned process group.
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    from pathlib import Path

    signal.signal(signal.SIGTERM, _cancel)
    signal.signal(signal.SIGINT, _cancel)
    serve(open_project(Path(sys.argv[1])), sys.argv[2], sys.stdin, sys.stdout)
