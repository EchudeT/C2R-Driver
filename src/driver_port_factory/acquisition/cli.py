from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..composition import open_project
from .closure import EvidenceClosureFinalizer
from .job import ArtifactOccurrence
from .proposal import EvidenceProposalImporter
from .repository import RepositoryAcquirer
from .revision_proposal import RevisionProposalImporter
from .revision_selection import RevisionSelector
from .verification import AcquisitionVerifier


def command_revisions(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    plan = RevisionSelector().select(
        project,
        proposal=ArtifactOccurrence(arguments.proposal_digest, arguments.proposal_ordinal),
    )
    print(json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True, indent=2))


def command_repositories(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    acquisition = RepositoryAcquirer().acquire(project)
    print(json.dumps(acquisition.to_dict(), ensure_ascii=False, sort_keys=True, indent=2))


def command_revision_proposal_import(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    occurrence = RevisionProposalImporter().import_job_result(
        project,
        job_digest=arguments.job_digest,
        job_ordinal=arguments.job_ordinal,
    )
    print(
        json.dumps(
            {"digest": occurrence.digest, "ordinal": occurrence.ordinal},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_proposal_import(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    imported = EvidenceProposalImporter().import_job_result(
        project,
        job_digest=arguments.job_digest,
        job_ordinal=arguments.job_ordinal,
    )
    print(
        json.dumps(
            {
                "proposal": {
                    "digest": imported.occurrence.digest,
                    "ordinal": imported.occurrence.ordinal,
                },
                "job_result": {
                    "digest": imported.job_result.digest,
                    "ordinal": imported.job_result.ordinal,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_closure_finalize(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    result = EvidenceClosureFinalizer().finalize(
        project,
        proposal=ArtifactOccurrence(arguments.proposal_digest, arguments.proposal_ordinal),
    )
    print(
        json.dumps(
            {
                "controlled_materials": result.controlled_materials,
                "explicit_gaps": result.explicit_gaps,
                "materials_manifest_digest": result.materials_manifest_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_repositories_verify(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    result = AcquisitionVerifier().verify(project)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


def register_commands(commands: CommandRegistry) -> None:
    acquire = commands.add_parser(
        "acquire", help="pin repositories and close provenance-tracked evidence"
    )
    subcommands = command_registry(acquire, dest="acquire_command")
    revisions = subcommands.add_parser("revisions")
    revisions.add_argument("path")
    revisions.add_argument("--proposal-digest", required=True)
    revisions.add_argument("--proposal-ordinal", required=True, type=int)
    revisions.set_defaults(handler=command_revisions)
    revision_proposal = subcommands.add_parser("revision-proposal-import")
    revision_proposal.add_argument("path")
    revision_proposal.add_argument("--job-digest", required=True)
    revision_proposal.add_argument("--job-ordinal", required=True, type=int)
    revision_proposal.set_defaults(handler=command_revision_proposal_import)
    repositories = subcommands.add_parser("repositories")
    repositories.add_argument("path")
    repositories.set_defaults(handler=command_repositories)
    proposal = subcommands.add_parser("proposal-import")
    proposal.add_argument("path")
    proposal.add_argument("--job-digest", required=True)
    proposal.add_argument("--job-ordinal", required=True, type=int)
    proposal.set_defaults(handler=command_proposal_import)
    finalize = subcommands.add_parser("closure-finalize")
    finalize.add_argument("path")
    finalize.add_argument("--proposal-digest", required=True)
    finalize.add_argument("--proposal-ordinal", required=True, type=int)
    finalize.set_defaults(handler=command_closure_finalize)
    verify = subcommands.add_parser("repositories-verify")
    verify.add_argument("path")
    verify.set_defaults(handler=command_repositories_verify)
