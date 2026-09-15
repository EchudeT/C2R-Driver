from __future__ import annotations

import argparse
from pathlib import Path

from ..cli_support import CommandRegistry
from ..composition import open_project
from .candidate import CandidateSealer


def command_seal(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    seal = CandidateSealer().seal(
        project, output=Path(arguments.output).resolve() if arguments.output else None
    )
    print(seal.digest)


def register_commands(commands: CommandRegistry) -> None:
    seal = commands.add_parser("seal", help="create the canonical candidate manifest")
    seal.add_argument("path")
    seal.add_argument("--output")
    seal.set_defaults(handler=command_seal)
