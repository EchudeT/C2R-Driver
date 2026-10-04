"""Small views of controller-owned repository locations, without acquisition side effects."""

import json
from pathlib import Path

from .repository import load_repository_acquisition


def repositories(project):
    acquired = load_repository_acquisition(project)
    return {
        "repositories": [
            {
                "role": row.role.value,
                "platform": row.platform,
                "requested_ref": row.requested_ref,
                "revision": row.resolved_commit,
                "path": str(project.root / row.checkout_path),
                "source_url": row.source_url,
            }
            for row in acquired.checkouts
        ],
        "target_worktree": str(project.root / acquired.target_worktree.path),
        "instruction": "Use these local paths directly. Baselines are frozen originals; only the "
        "target_worktree is the implementation tree. Full acquisition logs remain in the manifest; "
        "do not page through it merely to recover paths, versions or origins.",
    }


def command(args):
    from ..composition import open_project
    from ..short_refs import emit

    project = open_project(Path(args.path), read_only=True, verify_artifacts=False)
    print(json.dumps(emit(repositories(project)), ensure_ascii=False, indent=2))
