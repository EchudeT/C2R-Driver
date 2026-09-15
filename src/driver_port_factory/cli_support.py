from __future__ import annotations

import argparse
from typing import Any, Protocol, cast


class CommandRegistry(Protocol):
    """Public structural view of argparse's subcommand registry."""

    def add_parser(self, name: str, **kwargs: Any) -> argparse.ArgumentParser: ...


def command_registry(parser: argparse.ArgumentParser, *, dest: str) -> CommandRegistry:
    return cast(CommandRegistry, parser.add_subparsers(dest=dest, required=True))
