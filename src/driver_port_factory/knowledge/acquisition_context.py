"""Local-first discovery inputs for evidence selection, before the project KB exists."""

import sys

from ..acquisition.navigation import repositories
from .shared_binding import freeze
from .shared_cli import query_project


def context(project):
    bound = freeze(project)
    locations = repositories(project)
    checkouts = locations["repositories"]
    cli = [sys.executable, "-m", "driver_port_factory.cli", "knowledge"]
    return {
        "shared_library": bound,
        "initial_matches": (
            [
                {
                    "role": c["role"],
                    "query": project.config.driver_name,
                    "packet": query_project(
                        project,
                        project.config.driver_name,
                        repository=c["role"],
                        limit=3,
                        budget=4000,
                    ),
                }
                for c in checkouts
            ]
            if bound
            else []
        ),
        "shared_search": [
            *cli,
            "shared-search",
            str(project.root),
            "--query",
            "<QUESTION>",
            "--repository",
            "<source|target|qemu>",
        ],
        "shared_show": (
            [
                *cli,
                "library",
                "show",
                bound["root"],
                "--snapshot",
                bound["snapshot"],
                "--entry",
                "<ENTRY_ID>",
            ]
            if bound
            else None
        ),
        "local_repositories": checkouts,
        "target_worktree": locations["target_worktree"],
        "locations_command": [
            sys.executable,
            "-m",
            "driver_port_factory.cli",
            "acquire",
            "locations",
            str(project.root),
        ],
        "instruction": (
            "Use the pinned shared library for a concrete location gap when configured; inspect "
            "originals in these frozen local repositories. Search results are navigation, not "
            "proof of completeness; an empty result is not proof of absence. Use repository_paths "
            "to select matching local originals. Do not fetch a web copy or probe its URL merely "
            "to confirm provenance already bound to local Git. Only seek external materials for "
            "a concrete unresolved gap, explaining the missing evidence in the existing rationale. "
            "The controller acquires selected URLs and records hashes/provenance; do not duplicate "
            "downloads or status probes. Online master/latest cannot replace the fixed revision. "
            "For a gap supported by collected originals, use gap.basis:[{lane,facet}] to cite "
            "controlled facets, instead of inventing an external retrieval. This remains a gap, "
            "not hardware evidence or an accepted historical conclusion."
        ),
    }
