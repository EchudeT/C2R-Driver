"""Stdio MCP bridge. All stdout is protocol; checks block inside the tool."""

import json
import signal
import sys

from ..composition import open_project
from ..core.models import WorkflowError
from ..migration import check_tools

MAX_REQUEST = 65536


def dispatch(project, job_id, name, arguments):
    if name == "knowledge_learn":
        from ..knowledge.learn_tool import run

        return run(project, job_id, arguments)
    if name == "platform":
        from ..platform.worker import run

        return run(project, job_id, arguments)
    if name == "check":
        return check_tools.check(project, job_id, arguments)
    if name == "analysis":
        from ..migration.route_tool import run

        return run(project, job_id, arguments)
    if name == "progress":
        from ..migration.progress_tool import submit

        return submit(project, job_id, arguments)
    if name == "debug":
        from ..migration.debugging import run

        return run(project, job_id, arguments)
    raise WorkflowError("Unknown managed tool")


def _active_stages(project, job_id):
    if project is None:
        return None, None
    from ..migration.route_tool import authorize as analysis_authorize
    from ..platform.worker import authorize

    try:
        return authorize(project, job_id), None
    except WorkflowError:
        try:
            return None, analysis_authorize(project, job_id)
        except WorkflowError:
            return None, None


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
        from ..migration.route_tool import tool as analysis_tool
        from ..platform.worker import tool as platform_tool

        stage, analysis_stage = _active_stages(project, job_id)
        from ..knowledge.learn_tool import tool as learn_tool
        from ..knowledge.shared_binding import project_binding

        learning = (
            [learn_tool()]
            if project is not None
            and project_binding(project)
            and (stage or analysis_stage) in {"target_platform_study", "driver_implementation"}
            else []
        )
        if analysis_stage == "target_platform_study" and stage is None:
            return {"tools": [analysis_tool(), *learning]}
        if project is not None and stage in {
            "environment_recovery",
            "target_platform_study",
            "migration_contracts",
        }:
            return {
                "tools": [platform_tool(), *learning]
                + ([analysis_tool()] if stage == "target_platform_study" else [])
            }
        tools = check_tools.tools()
        tools.extend(learning)
        if project is not None and stage is not None:
            tools.append(platform_tool())
        from ..migration.debugging import tool

        tools.append(tool())
        if project is not None and project.config.behavior_scheduling:
            from ..migration.progress_tool import tool as progress_tool

            tools.append(progress_tool())
            tools.append(analysis_tool())
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
