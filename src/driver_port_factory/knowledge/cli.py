from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..composition import open_project
from ..core.models import WorkflowError
from .contracts import KnowledgeDomain
from .embeddings import build_configured
from .index import KnowledgeIndex


def _json(value, **kwargs):
    from ..short_refs import emit

    return json.dumps(emit(value), **kwargs)


def _query_index(arguments: argparse.Namespace) -> KnowledgeIndex:
    # CorpusManifest and KnowledgeIndex validate this query's frozen dependencies.
    # Unrelated runtime images are verified at stage acceptance, not on every lookup.
    project = open_project(Path(arguments.path), read_only=True, verify_artifacts=False)
    return KnowledgeIndex.for_project(project)


def command_rebuild(arguments: argparse.Namespace) -> None:
    print(
        _json(
            build_configured(KnowledgeIndex.for_project(open_project(Path(arguments.path)))),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_status(arguments: argparse.Namespace) -> None:
    print(
        _json(
            _query_index(arguments).status(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_inventory(arguments: argparse.Namespace) -> None:
    print(
        _json(
            _query_index(arguments).inventory(domain=arguments.domain),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_search(arguments: argparse.Namespace) -> None:
    print(
        _json(
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
        _json(
            _query_index(arguments).show(arguments.chunk_id),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_search_batch(arguments: argparse.Namespace) -> None:
    try:
        queries = json.loads(arguments.queries_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise WorkflowError(f"cannot read batch query file: {error}") from error
    print(
        _json(
            _query_index(arguments).search_many(queries, compact=not arguments.full),
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
    from .problem_packet import register as register_packet

    register_packet(subcommands)
    from .rag_cli import register as register_rag

    register_rag(subcommands)
    from .shared_cli import register as register_shared

    register_shared(subcommands)
    from .probes import register as register_probes

    register_probes(subcommands)
    from .translation_facts import TOPICS

    facts = subcommands.add_parser("facts", help="bounded source/target translation navigation")
    facts.add_argument("path")
    facts.add_argument("--topic", choices=list(TOPICS))
    facts.add_argument("--domain", type=KnowledgeDomain, choices=list(KnowledgeDomain))
    facts.add_argument("--limit", type=int, default=12)
    facts.set_defaults(handler=command_facts)
    semantic = subcommands.add_parser("semantic", help="on-demand Clang exact-symbol query")
    semantic.add_argument("path")
    semantic.add_argument("--compile-db", required=True)
    semantic.add_argument("--file", required=True)
    semantic.add_argument("--symbol", required=True)
    semantic.add_argument("--limit", type=int, default=8)
    semantic.set_defaults(handler=command_semantic)
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
    batch = subcommands.add_parser(
        "search-batch", help="query one verified snapshot with shared evidence"
    )
    batch.add_argument("path")
    batch.add_argument("--queries-file", type=Path, required=True)
    batch.add_argument("--full", action="store_true")
    batch.set_defaults(handler=command_search_batch)
    show = subcommands.add_parser("show")
    show.add_argument("path")
    show.add_argument("--chunk-id", required=True)
    show.set_defaults(handler=command_show)


def command_facts(arguments):
    from .translation_facts import query

    project = open_project(Path(arguments.path), read_only=True, verify_artifacts=False)
    print(
        _json(
            query(project, topic=arguments.topic, domain=arguments.domain, limit=arguments.limit),
            ensure_ascii=False,
            indent=2,
        )
    )


def command_semantic(arguments):
    from .semantic import query

    project = open_project(Path(arguments.path), read_only=True, verify_artifacts=False)
    print(
        _json(
            query(
                project,
                arguments.compile_db,
                arguments.file,
                arguments.symbol,
                limit=arguments.limit,
            ),
            indent=2,
        )
    )
