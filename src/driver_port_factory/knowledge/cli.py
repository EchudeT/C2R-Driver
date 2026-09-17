from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..composition import open_project
from .bootstrap import KnowledgeBootstrapper
from .contracts import KnowledgeDomain
from .index import KnowledgeIndex


def _query_index(arguments: argparse.Namespace) -> KnowledgeIndex:
    project = open_project(Path(arguments.path), read_only=True)
    return KnowledgeIndex.for_project(project)


def command_bootstrap(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    result = KnowledgeBootstrapper().bootstrap(project, probe_plan_path=Path(arguments.probe_plan))
    print(
        json.dumps(
            {
                "readiness": result.readiness.value,
                "stage_status": result.stage_status.value,
                "generated_skill_path": result.generated_skill_path,
                "failed_probe_ids": list(result.failed_probe_ids),
                "errors": list(result.errors),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_rebuild(arguments: argparse.Namespace) -> None:
    print(
        json.dumps(
            KnowledgeIndex.for_project(open_project(Path(arguments.path))).build(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_status(arguments: argparse.Namespace) -> None:
    print(
        json.dumps(
            _query_index(arguments).status(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_inventory(arguments: argparse.Namespace) -> None:
    print(
        json.dumps(
            _query_index(arguments).inventory(domain=arguments.domain),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_search(arguments: argparse.Namespace) -> None:
    print(
        json.dumps(
            _query_index(arguments).search(
                arguments.query,
                domain=arguments.domain,
                record_id=arguments.record_id,
                path_prefix=arguments.path_prefix,
                limit=arguments.limit,
            ),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_show(arguments: argparse.Namespace) -> None:
    print(
        json.dumps(
            _query_index(arguments).show(arguments.chunk_id),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def register_commands(commands: CommandRegistry) -> None:
    knowledge = commands.add_parser(
        "knowledge", help="manage the provenance-checked local knowledge base"
    )
    subcommands = command_registry(knowledge, dest="knowledge_command")
    bootstrap = subcommands.add_parser("bootstrap")
    bootstrap.add_argument("path")
    bootstrap.add_argument("--probe-plan", required=True)
    bootstrap.set_defaults(handler=command_bootstrap)
    rebuild = subcommands.add_parser("rebuild")
    rebuild.add_argument("path")
    rebuild.set_defaults(handler=command_rebuild)
    status = subcommands.add_parser("status")
    status.add_argument("path")
    status.set_defaults(handler=command_status)
    inventory = subcommands.add_parser("inventory")
    inventory.add_argument("path")
    inventory.add_argument("--domain", type=KnowledgeDomain, choices=list(KnowledgeDomain))
    inventory.set_defaults(handler=command_inventory)
    search = subcommands.add_parser("search")
    search.add_argument("path")
    search.add_argument("--query", required=True)
    search.add_argument("--domain", type=KnowledgeDomain, choices=list(KnowledgeDomain))
    search.add_argument("--record-id")
    search.add_argument("--path-prefix")
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(handler=command_search)
    show = subcommands.add_parser("show")
    show.add_argument("path")
    show.add_argument("--chunk-id", required=True)
    show.set_defaults(handler=command_show)
