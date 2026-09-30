"""Expand selected repository directories from frozen Git trees, without model work."""

import json
import subprocess
from dataclasses import replace

from ..core.models import WorkflowError
from .locators import GitBlobLocator
from .repository import load_repository_acquisition


def expand_directories(project, proposal):
    acquisition = load_repository_acquisition(project)
    selections = {}
    for facet in proposal.facets:
        for locator in facet.locators:
            if isinstance(locator, GitBlobLocator):
                selections.setdefault(locator.repository, set()).add(locator.path)
    trees = {}
    for role, paths in selections.items():
        checkout = acquisition.checkout(role)
        result = subprocess.run(
            ["git", "--literal-pathspecs", "-C", str(project.root / checkout.checkout_path),
             "ls-tree", "-r", "-z", "--full-tree", checkout.resolved_commit, "--", *sorted(paths)],
            capture_output=True, check=False,
        )
        if result.returncode:
            raise WorkflowError("cannot enumerate frozen evidence selection: " +
                                result.stderr.decode("utf-8", errors="replace").strip())
        trees[role] = _paths(result.stdout)
    facets = []
    for facet in proposal.facets:
        locators, seen = [], set()
        for locator in facet.locators:
            expanded = [locator]
            if isinstance(locator, GitBlobLocator):
                matches = [path for path in trees[locator.repository]
                           if path == locator.path or path.startswith(locator.path + "/")]
                # Keep missing selections for ordinary retrieval/gap accounting.
                # Do not silently drop missing files or convert them to successful evidence.
                if matches:
                    expanded = [replace(locator, path=path) for path in matches]
            for item in expanded:
                identity = json.dumps(item.to_dict(), sort_keys=True)
                if identity not in seen:
                    seen.add(identity)
                    locators.append(item)
        facets.append(replace(facet, locators=tuple(locators)))
    return replace(proposal, facets=tuple(facets))


def _paths(raw):
    paths = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path = record.split(b"\t", 1)
            _, object_type, _ = metadata.decode("ascii").split(" ", 2)
            if object_type not in {"blob", "commit"}:
                raise ValueError("unexpected non-leaf tree entry")
            # Keep symlinks/gitlinks visible to the normal retrieval checks too.
            paths.append(path.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise WorkflowError("malformed frozen evidence Git tree output") from error
    return tuple(sorted(paths))
