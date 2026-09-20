from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..composition import open_project
from ..source_analysis.query import query_symbols
from ..source_analysis.navigation import prepare_navigation, require_analysis_ready
from .contracts import KnowledgeDomain
from .index import KnowledgeIndex


def _query_index(arguments: argparse.Namespace) -> KnowledgeIndex:
    # CorpusManifest and KnowledgeIndex validate this query's frozen dependencies.
    # Unrelated runtime images are verified at stage acceptance, not on every lookup.
    project = open_project(Path(arguments.path), read_only=True, verify_artifacts=False)
    return KnowledgeIndex.for_project(project)


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
                compact=not arguments.full,
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


def command_c_facts(arguments: argparse.Namespace) -> None:
    # Navigation verifies its facts, semantic inputs and derived database; old
    # QEMU images and unrelated historical outputs are not query dependencies.
    project = open_project(Path(arguments.path), read_only=True, verify_artifacts=False)
    require_analysis_ready(project)
    results = query_symbols(
        project, symbols=arguments.symbol, source_path=arguments.source_path,
        limit=arguments.limit, detail=arguments.detail,
    )
    print(json.dumps(results[0] if len(results) == 1 else {"queries": results},
                     ensure_ascii=False, indent=2))


def command_c_facts_build(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path), read_only=True)
    require_analysis_ready(project)
    prepare_navigation(project)
    print(json.dumps({"navigation": "ready"}))


def register_commands(commands: CommandRegistry) -> None:
    knowledge = commands.add_parser(
        "knowledge", help="manage the provenance-checked local knowledge base"
    )
    subcommands = command_registry(knowledge, dest="knowledge_command")
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
    search.add_argument(
        "--full", action="store_true", help="include full chunks instead of summaries"
    )
    search.set_defaults(handler=command_search)
    show = subcommands.add_parser("show")
    show.add_argument("path")
    show.add_argument("--chunk-id", required=True)
    show.set_defaults(handler=command_show)
    facts = subcommands.add_parser(
        "c-facts", help="inspect a named function/type in frozen C facts"
    )
    facts.add_argument("path")
    facts.add_argument("--symbol", required=True, action="append",
                       help="repeat for a batch (up to 32); each index is scanned once")
    facts.add_argument("--source-path")
    facts.add_argument("--limit", type=int, default=5)
    facts.add_argument("--detail", choices=("summary", "calls", "cfg"), default="summary",
                       help="fetch full calls or compiler CFG only for the selected symbol")
    facts.set_defaults(handler=command_c_facts)
    build = subcommands.add_parser(
        "c-facts-build", help="prepare derived symbol navigation outside the worker sandbox"
    )
    build.add_argument("path")
    build.set_defaults(handler=command_c_facts_build)
