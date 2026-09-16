from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..composition import open_project
from .handoff import MigrationHandoff


def command_handoff(arguments: argparse.Namespace) -> None:
    record = MigrationHandoff().create(open_project(Path(arguments.path)))
    print(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2))


def register_commands(commands: CommandRegistry) -> None:
    migration = commands.add_parser("migration", help="run controlled migration transitions")
    subcommands = command_registry(migration, dest="migration_command")
    handoff = subcommands.add_parser("handoff")
    handoff.add_argument("path")
    handoff.set_defaults(handler=command_handoff)
