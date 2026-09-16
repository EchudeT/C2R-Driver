from __future__ import annotations

from pathlib import Path

from ..core.models import WorkflowError
from .authority import RepositoryEndorsementAuthority
from .locators import ExternalUrlLocator
from .repository_filesystem import git_bytes, git_output
from .repository_manifest import RepositoryAcquisition


def validate_repository_endorsement(
    root: Path,
    acquisition: RepositoryAcquisition,
    locator: ExternalUrlLocator,
    authority: RepositoryEndorsementAuthority,
) -> None:
    checkout = acquisition.checkout(authority.repository)
    checkout_root = root / checkout.checkout_path
    blob = git_output(
        root,
        "-C",
        str(checkout_root),
        "rev-parse",
        f"{checkout.resolved_commit}:{authority.path}",
    )
    content = git_bytes(root, "-C", str(checkout_root), "cat-file", "blob", blob)
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise WorkflowError("repository endorsement source is not UTF-8") from error
    if authority.line_end > len(lines):
        raise WorkflowError("repository endorsement source span drifted")
    excerpt = "\n".join(lines[authority.line_start - 1 : authority.line_end])
    if locator.source_url not in excerpt:
        raise WorkflowError("repository endorsement no longer references the external URL")
