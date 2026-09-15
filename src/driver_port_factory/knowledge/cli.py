from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..composition import open_project
from .bootstrap import KnowledgeBootstrapper
from .contracts import KnowledgeDomain, MaterialRedistribution
from .index import KnowledgeIndex
from .materials import KnowledgeMaterialRegistrar


def command_add(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    record = KnowledgeMaterialRegistrar().add(
        project,
        identifier=arguments.identifier,
        domain=arguments.domain,
        path=Path(arguments.file),
        source_url=arguments.source_url,
        revision=arguments.revision,
        license_note=arguments.license,
        redistribution=arguments.redistribution,
        category=arguments.category,
        authority=arguments.authority,
        original=not arguments.derived,
        index=not arguments.no_index,
        notes=arguments.notes,
    )
    print(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2))


def command_gap(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    record = KnowledgeMaterialRegistrar().add_gap(
        project,
        identifier=arguments.identifier,
        domain=arguments.domain,
        reason=arguments.reason,
        revision=arguments.revision,
        category=arguments.category,
    )
    print(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2))


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
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_rebuild(arguments: argparse.Namespace) -> None:
    print(
        json.dumps(
            KnowledgeIndex(Path(arguments.path)).build(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_status(arguments: argparse.Namespace) -> None:
    print(
        json.dumps(
            KnowledgeIndex(Path(arguments.path)).status(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_inventory(arguments: argparse.Namespace) -> None:
    print(
        json.dumps(
            KnowledgeIndex(Path(arguments.path)).inventory(domain=arguments.domain),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_search(arguments: argparse.Namespace) -> None:
    print(
        json.dumps(
            KnowledgeIndex(Path(arguments.path)).search(
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
            KnowledgeIndex(Path(arguments.path)).show(arguments.chunk_id),
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
    add = subcommands.add_parser("add")
    add.add_argument("path")
    add.add_argument("--id", dest="identifier", required=True)
    add.add_argument("--domain", type=KnowledgeDomain, choices=list(KnowledgeDomain), required=True)
    add.add_argument("--file", required=True)
    add.add_argument("--source-url", required=True)
    add.add_argument("--revision", required=True)
    add.add_argument("--license", default="review-required")
    add.add_argument(
        "--redistribution",
        type=MaterialRedistribution,
        choices=list(MaterialRedistribution),
        default=MaterialRedistribution.UNKNOWN,
    )
    add.add_argument("--category")
    add.add_argument("--authority")
    add.add_argument("--derived", action="store_true")
    add.add_argument("--no-index", action="store_true")
    add.add_argument("--notes")
    add.set_defaults(handler=command_add)
    gap = subcommands.add_parser("gap")
    gap.add_argument("path")
    gap.add_argument("--id", dest="identifier", required=True)
    gap.add_argument("--domain", type=KnowledgeDomain, choices=list(KnowledgeDomain), required=True)
    gap.add_argument("--reason", required=True)
    gap.add_argument("--revision", required=True)
    gap.add_argument("--category", required=True)
    gap.set_defaults(handler=command_gap)
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
