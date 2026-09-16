from __future__ import annotations

import argparse
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..composition import open_project
from .candidate import CandidateSealer


def command_seal(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    seal = CandidateSealer().seal(
        project,
        request_path=Path(arguments.request),
        receipt_path=Path(arguments.timestamp_receipt),
        output=Path(arguments.output),
    )
    print(seal.digest)


def register_commands(commands: CommandRegistry) -> None:
    candidate = commands.add_parser("candidate", help="manage the immutable migration candidate")
    candidate_commands = command_registry(candidate, dest="candidate_command")
    seal = candidate_commands.add_parser("seal", help="seal and transfer one candidate attempt")
    seal.add_argument("path")
    seal.add_argument("--request", required=True)
    seal.add_argument("--timestamp-receipt", required=True)
    seal.add_argument("--output", required=True)
    seal.set_defaults(handler=command_seal)
