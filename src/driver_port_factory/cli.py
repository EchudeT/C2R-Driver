from __future__ import annotations

import argparse
import json
import sys

from .acquisition.cli import register_commands as register_acquisition_commands
from .cli_support import command_registry
from .codex.cli import register_commands as register_codex_commands
from .control.cli import register_commands as register_control_commands
from .core.models import WorkflowError
from .environment.cli import register_commands as register_environment_commands
from .intake.cli import register_commands as register_intake_commands
from .knowledge.cli import register_commands as register_knowledge_commands
from .migration.cli import register_commands as register_migration_commands
from .port import register_commands as register_port_commands
from .sealing.cli import register_commands as register_sealing_commands
from .source_analysis.cli import register_commands as register_source_analysis_commands
from .target_study.cli import register_commands as register_target_study_commands


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="dpf", description="Driver Port Factory")
    commands = command_registry(root, dest="command")
    register_control_commands(commands)
    register_port_commands(commands)
    register_intake_commands(commands)
    register_acquisition_commands(commands)
    register_environment_commands(commands)
    register_knowledge_commands(commands)
    register_target_study_commands(commands)
    register_migration_commands(commands)
    register_source_analysis_commands(commands)
    register_codex_commands(commands)
    register_sealing_commands(commands)
    return root


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = parser().parse_args(argv)
        arguments.handler(arguments)
    except (WorkflowError, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
